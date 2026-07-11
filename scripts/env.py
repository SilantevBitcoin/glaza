"""Read the skill's .env (stdlib only). OS env vars win over file values.

Only the skill's own .env is read. The current working directory is NOT scanned:
/glaza runs from whatever project the user happens to be in, and that project's
.env is none of our business (and could silently override WATCH_* keys).
"""
from __future__ import annotations
import os
from pathlib import Path


def _clean_value(val: str) -> str:
    val = val.strip()
    if len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
        return val[1:-1]
    # inline-комментарий: `WATCH_FPS=1  # плотность` -> `1`.
    # Режем только по ` #` — значение вида `pass#word` остаётся целым.
    head, sep, _ = val.partition(" #")
    return head.strip() if sep else val


def read_env(paths: list[Path] | None = None) -> dict[str, str]:
    if paths is None:
        paths = [Path(__file__).resolve().parents[1] / ".env"]
    cfg: dict[str, str] = {}
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key:
                cfg[key] = _clean_value(val)
    for key in list(cfg) + [k for k in os.environ if k.startswith(("WATCH_", "OV_", "FW_", "WHISPERCPP_"))]:
        if key in os.environ and os.environ[key].strip():
            cfg[key] = os.environ[key].strip()
    return cfg


def write_env(path: Path, values: dict[str, str]) -> None:
    """Обновляет только переданные ключи. Чужие строки и комментарии сохраняются.

    Значение пишется как есть: путь к модели содержит `\\` и `:` — экранировать нечего,
    read_env читает строку до первого `=` и режет только инлайн-комментарий (` #`).
    """
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    remaining = dict(values)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = (line.split("=", 1)[0].strip()
               if "=" in line and not stripped.startswith("#") else None)
        if key and key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in remaining.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
