"""Preflight for /glaza. Silent on success. Exit: 0 ok / 2 bins|packages / 3 backend.

Everything checked here must be checked BEFORE the video is downloaded — a preflight
that passes and then dies on the transcription step has cost the user a download.
"""
from __future__ import annotations
import importlib.util, shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from env import read_env

REQUIRED_BINS = ["ffmpeg", "ffprobe"]
# yt-dlp нужен только для URL-источника; локальный файл работает без него.
OPTIONAL_BINS = {"yt-dlp": "нужен только для скачивания по ссылке (pip install -U yt-dlp)"}
# Pillow — dHash-дедуп кадров (dedup.py). Без него скилл падает уже после закачки.
REQUIRED_PY = {"PIL": "Pillow"}


def _backend_problem(cfg: dict) -> str | None:
    """None = бэкенд готов; иначе строка с конкретной причиной."""
    backend = cfg.get("WATCH_WHISPER", "faster")
    if backend == "faster":
        if importlib.util.find_spec("faster_whisper") is None:
            return "пакет faster-whisper не установлен (`pip install faster-whisper`)"
        return None
    if backend == "ov":
        return _missing_paths(cfg, ("OV_PYTHON", "OV_MODEL"))
    if backend == "whispercpp":
        return _missing_paths(cfg, ("WHISPERCPP_BIN", "WHISPERCPP_MODEL"))
    return f"неизвестный бэкенд {backend!r} (ov|faster|whispercpp)"


def _missing_paths(cfg: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = cfg.get(key, "")
        if not value:
            return f"{key} не задан в .env"
        if not Path(value).expanduser().exists():
            return f"{key} указывает на несуществующий путь: {value}"
    return None


def status(cfg: dict) -> dict:
    problem = _backend_problem(cfg)
    return {
        "missing_bins": [b for b in REQUIRED_BINS if shutil.which(b) is None],
        "missing_py": [pkg for mod, pkg in REQUIRED_PY.items()
                       if importlib.util.find_spec(mod) is None],
        "missing_optional": [b for b in OPTIONAL_BINS if shutil.which(b) is None],
        "backend": cfg.get("WATCH_WHISPER", "faster"),
        "backend_ready": problem is None,
        "backend_problem": problem,
    }


def cmd_check() -> int:
    s = status(read_env())
    if s["missing_bins"]:
        sys.stderr.write(f"[glaza] missing: {', '.join(s['missing_bins'])} (установи ffmpeg)\n")
        return 2
    if s["missing_py"]:
        sys.stderr.write(f"[glaza] missing python packages: {', '.join(s['missing_py'])} "
                         f"(`pip install {' '.join(s['missing_py'])}`)\n")
        return 2
    if not s["backend_ready"]:
        sys.stderr.write(f"[glaza] Whisper-бэкенд '{s['backend']}' не готов: "
                         f"{s['backend_problem']} — см. .env.example\n")
        return 3
    for b in s["missing_optional"]:
        sys.stderr.write(f"[glaza] warning: {b} не найден — {OPTIONAL_BINS[b]}\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        raise SystemExit(cmd_check())
    print("Configure .env from .env.example; see README.")
