"""Adapter: call the OpenVINO worker via OV_PYTHON, read its JSON output file."""
from __future__ import annotations
import json, os, subprocess
from pathlib import Path


def build_ov_argv(cfg: dict, audio: str, lang: str, out_json: str) -> list[str]:
    worker = str(Path(__file__).resolve().parent / "whisper_ov_worker.py")
    return [cfg["OV_PYTHON"], worker, cfg["OV_MODEL"], str(Path(audio).resolve()),
            lang or "auto", cfg.get("OV_DEVICE", "GPU"), out_json]


def transcribe_ov(audio: str, lang: str, cfg: dict) -> list[dict]:
    for k in ("OV_PYTHON", "OV_MODEL"):
        if not cfg.get(k):
            raise SystemExit(f"{k} not set in .env (needed for ov backend)")
    out_json = str(Path(audio).with_suffix(".ov.json"))
    env = {**os.environ, "ONEDNN_VERBOSE": "0"}   # quiet oneDNN diagnostics
    r = subprocess.run(build_ov_argv(cfg, audio, lang, out_json),
                       capture_output=True, text=True, encoding="utf-8", env=env)
    if r.returncode != 0:
        raise SystemExit(f"ov worker failed: {r.stderr.strip()[-500:]}")
    try:
        return json.loads(Path(out_json).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"ov worker produced no valid JSON ({exc}); stderr tail: {r.stderr.strip()[-300:]}")
