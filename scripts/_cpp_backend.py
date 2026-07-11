"""Adapter: whisper.cpp CLI (`whisper-cli -oj`) → JSON → segments."""
from __future__ import annotations
import json, subprocess
from pathlib import Path


def build_cpp_argv(cfg: dict, audio: str, lang: str) -> list[str]:
    argv = [cfg["WHISPERCPP_BIN"], "-m", cfg["WHISPERCPP_MODEL"],
            "-f", str(Path(audio).resolve()), "-oj", "-of", str(Path(audio).with_suffix(""))]
    if lang and lang != "auto":
        argv += ["-l", lang]
    return argv


def parse_cpp_json(text: str) -> list[dict]:
    data = json.loads(text)
    out = []
    for seg in data.get("transcription", []):
        off = seg.get("offsets", {})
        out.append({"start": round(off.get("from", 0) / 1000.0, 2),
                    "end": round(off.get("to", 0) / 1000.0, 2),
                    "text": (seg.get("text") or "").strip()})
    return out


def transcribe_cpp(audio: str, lang: str, cfg: dict) -> list[dict]:
    for k in ("WHISPERCPP_BIN", "WHISPERCPP_MODEL"):
        if not cfg.get(k):
            raise SystemExit(f"{k} not set in .env (needed for whispercpp backend)")
    argv = build_cpp_argv(cfg, audio, lang)
    r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise SystemExit(f"whisper.cpp failed: {r.stderr.strip()}")
    json_path = Path(audio).with_suffix(".json")
    text = json_path.read_text(encoding="utf-8") if json_path.exists() else r.stdout
    return parse_cpp_json(text)
