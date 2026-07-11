"""Frame extraction for /glaza.

Two jobs, two qualities:
  * `extract_fps1` — dense, length-proportional sampling for the vision pass.
    Downscaled, `-q:v 3`; these frames are read by subagents, never shown to the user.
  * `extract_one` — a single frame at native resolution, `-q:v 2`, for the digest.

Also a CLI so Step 6 of SKILL.md can call `extract_one` without an inline `python -c`.
"""
from __future__ import annotations
import argparse, json, re, shutil, subprocess, sys
from pathlib import Path

_PTS_RE = re.compile(r"pts_time:([0-9.]+)")


def format_time(seconds: float) -> str:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_rep(rep: dict) -> str:
    """Строка представителя для отчётов: человеку — таймкод, скрипту — секунды.
    `span_end` — последний кадр серии; именно его переизвлекает `extract-one`."""
    ts = rep["timestamp"]
    end_ts = rep.get("span_end_timestamp", ts)
    span = f" → span_end={format_time(end_ts)} [{end_ts:.2f}s]" if end_ts > ts else ""
    return f"- `{rep['path']}` (t={format_time(ts)} [{ts:.2f}s]{span})"


def parse_time(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        pass
    raise SystemExit(f"Cannot parse time: {value!r} (SS, MM:SS, HH:MM:SS)")


def _parse_rate(value) -> float | None:
    """ffprobe даёт частоту дробью-строкой (`"30000/1001"`, `"10/1"`). Вернуть
    кадры/с или None, если дробь пустая/нулевая/битая (`"0/0"`, `"N/A"`)."""
    if not value:
        return None
    try:
        num, _, den = str(value).partition("/")
        den_f = float(den) if den else 1.0
        return float(num) / den_f if den_f else None
    except (ValueError, ZeroDivisionError):
        return None


def get_metadata(video_path: str) -> dict:
    if shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe not installed (part of ffmpeg).")
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(Path(video_path).resolve())],
        capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise SystemExit(f"ffprobe failed: {r.stderr.strip()}")
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams", [])
    vs = next((s for s in streams if s.get("codec_type") == "video"), {})
    aud = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    dur = float(fmt.get("duration") or vs.get("duration") or 0)
    # avg_frame_rate = кадров/длительность (реальная средняя частота). На VFR
    # r_frame_rate врёт — это номинал таймбейза (LCD интервалов), на скринкасте
    # часто 25/30/1000 при эффективных единицах кадров/с. Фолбэк на r_frame_rate,
    # только если avg отсутствует.
    fps = _parse_rate(vs.get("avg_frame_rate")) or _parse_rate(vs.get("r_frame_rate")) or 0.0
    return {"duration_seconds": dur, "width": vs.get("width"),
            "height": vs.get("height"), "has_audio": aud is not None,
            "fps": round(fps, 3)}


def extract_fps1(video, out_dir: Path, width: int, fps: float = 1.0,
                 start: float | None = None, end: float | None = None) -> list[dict]:
    """Uniform frame-per-second extraction — dense, length-proportional.

    Density is fixed by `fps`, so the frame count scales with video length; there is
    no max-frame cap. Downstream dedup (dedup.py) collapses the near-duplicate runs
    this produces (a static talking head, a slide held on screen).

    `start`/`end` trim the decode itself (`-ss`/`-to` before `-i`), covering the
    half-open window [start, end). Without them a focused re-pass over one demo zone
    would slice the entire video. Output pts restart at zero, so frame N maps to
    `start + N/fps`.

    Frames are never upscaled: `scale='min(iw,width)'` leaves a source narrower than
    `width` at its native size. An upscaled frame costs a vision subagent the same
    tokens as a real one (⌈w/28⌉×⌈h/28⌉) and carries no extra detail.
    Returns [{index, timestamp, path}].
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg not installed.")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("frame_*.jpg"):
        f.unlink()
    offset = start or 0.0
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-i", str(Path(video).resolve()),
            "-vf", f"fps={fps},scale='min(iw,{int(width)})':-2", "-q:v", "3",
            str(out_dir / "frame_%05d.jpg")]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg fps extraction failed: {r.stderr.strip()}")
    files = sorted(out_dir.glob("frame_*.jpg"))
    return [{"index": i, "timestamp": round(offset + i / fps, 2), "path": str(p)}
            for i, p in enumerate(files)]


def extract_scene_changes(video, out_dir: Path, width: int, start: float, end: float,
                          scene_threshold: float = 0.1, min_frames: int = 3) -> list[dict]:
    """Frames on SCENE CHANGES in the window [start, end) — content-aware, not the
    uniform grid of extract_fps1.

    A uniform fps=N pass misses a state shorter than 1/N seconds (a toggle held for
    0.2 s). `select='gt(scene,threshold)'` fires on the change itself, so the event
    lands whenever it happens. A smooth stretch with fewer than `min_frames` cuts
    falls back to extract_fps1 over the same window (nothing to key on otherwise).

    Timecodes are ABSOLUTE. `-ss`/`-to` before `-i` reset pts to zero (so the window
    trims the decode, not the whole video), hence ts = start + pts_time. pts comes
    from `showinfo` with `-loglevel info`: `metadata=print` yields no timestamps on
    ffmpeg 8.x. Frames are never upscaled (`scale='min(iw,width)'`).
    Returns [{index, timestamp, path}] like extract_fps1.
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg not installed.")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("frame_*.jpg"):
        f.unlink()
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "info", "-y",
           "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(Path(video).resolve()),
           "-vf", (f"select='gt(scene,{scene_threshold})',showinfo,"
                   f"scale='min(iw,{int(width)})':-2"),
           "-vsync", "vfr", "-q:v", "3", str(out_dir / "frame_%05d.jpg")]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    files = sorted(out_dir.glob("frame_*.jpg"))
    # A window with zero scene cuts makes the image2 muxer exit non-zero ("nothing
    # was written") — that is a legitimate "no changes" signal, not a failure. Decide
    # by the frame count: too few → fall back to uniform sampling (which re-decodes
    # the window and will surface a genuinely broken video on its own).
    if len(files) < min_frames:
        return extract_fps1(video, out_dir, width=width, fps=1.0, start=start, end=end)
    pts = [float(m) for m in _PTS_RE.findall(r.stderr)]
    n = min(len(files), len(pts))
    return [{"index": i, "timestamp": round(start + pts[i], 2), "path": str(files[i])}
            for i, p in enumerate(files[:n])]


def extract_one(video, timestamp: float, out_path, width=None) -> str:
    """Extract ONE frame at `timestamp` (seconds), NATIVE resolution by default
    (width=None) or scaled to `width`. High quality (`-q:v 2`). Used for final
    digest screenshots that must stay readable — unlike the downscaled vision
    frames from extract_fps1. Returns the output path."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg not installed.")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{float(timestamp):.3f}", "-i", str(Path(video).resolve()),
           "-frames:v", "1", "-q:v", "2"]
    if width:
        cmd += ["-vf", f"scale={int(width)}:-2"]
    cmd += [str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg extract_one failed: {r.stderr.strip()}")
    return str(out_path)


def main() -> int:
    ap = argparse.ArgumentParser(prog="frames", description="glaza frame helpers")
    sub = ap.add_subparsers(dest="cmd", required=True)
    one = sub.add_parser("extract-one", help="один кадр по таймкоду, родное разрешение")
    one.add_argument("--video", required=True)
    one.add_argument("--ts", required=True, help="секунды или MM:SS / HH:MM:SS")
    one.add_argument("--out", required=True)
    one.add_argument("--width", type=int, default=None, help="масштаб (по умолчанию — родное)")
    args = ap.parse_args()
    if args.cmd == "extract-one":
        print(extract_one(args.video, parse_time(args.ts), args.out, width=args.width))
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")   # Windows cp1251 console
    raise SystemExit(main())
