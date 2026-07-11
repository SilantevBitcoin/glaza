"""Resolve a video source: yt-dlp download for URLs, direct path for local files.
argv-injection hardening adapted from claude-watch (MIT) — see NOTICE."""
from __future__ import annotations
import json, shutil, subprocess, sys
from pathlib import Path
from urllib.parse import urlparse

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv"}


def is_url(source: str) -> bool:
    if source.startswith("-"):
        return False
    p = urlparse(source)
    return p.scheme in ("http", "https") and bool(p.netloc)


def build_ytdlp_argv(url: str, out_template: str) -> list[str]:
    return [
        "yt-dlp",
        "-N", "8",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/bv+ba/b",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--no-playlist",
        "--ignore-errors",
        "-o", out_template,
        "--", url,
    ]


def _pick_video(out_dir: Path) -> Path | None:
    for ext in (".mp4", ".mkv", ".webm", ".mov"):
        for c in out_dir.glob(f"video*{ext}"):
            return c
    for c in out_dir.glob("video.*"):
        if c.suffix.lower() in VIDEO_EXTS:
            return c
    return None


def _resolve_local(path: str) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise SystemExit(f"File not found: {p}")
    if p.suffix.lower() not in VIDEO_EXTS:
        print(f"[glaza] warning: {p.suffix} is not a known video extension", file=sys.stderr)
    return {"video_path": str(p), "title": p.stem, "duration": None, "is_local": True}


def fetch_source(source: str, work_dir: Path) -> dict:
    if not is_url(source):
        return _resolve_local(source)
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp not installed. Install: pip install -U yt-dlp")
    work_dir.mkdir(parents=True, exist_ok=True)
    argv = build_ytdlp_argv(source, str(work_dir / "video.%(ext)s"))
    result = subprocess.run(argv, stdout=sys.stderr, stderr=sys.stderr)
    video = _pick_video(work_dir)
    if video is None:
        raise SystemExit(
            f"yt-dlp produced no video in {work_dir} (exit {result.returncode}). "
            "If YouTube changed, update: pip install -U yt-dlp"
        )
    info_path = work_dir / "video.info.json"
    title, duration = video.stem, None
    if info_path.exists():
        try:
            raw = json.loads(info_path.read_text(encoding="utf-8"))
            title = raw.get("title") or title
            duration = raw.get("duration")
        except Exception as exc:
            print(f"[glaza] info.json parse failed: {exc}", file=sys.stderr)
    return {"video_path": str(video), "title": title, "duration": duration, "is_local": False}
