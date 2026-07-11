"""/glaza entry point (v2 «видео → конспект»): source -> dense fps frames +
pHash-dedup + local transcript -> stdout. Claude reads the representative
frames, runs vision fan-out per SKILL.md, then builds the конспект."""
from __future__ import annotations
import argparse, sys, tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from env import read_env
from source import fetch_source
import frames as F
import dedup as DD
import transcribe as T

# Текст — ядро конспекта. Если транскрипт не получен из-за поломки бэкенда,
# это не «видео без звука»: собирать конспект нельзя, и exit-код обязан это сказать.
EXIT_NO_TRANSCRIPT = 4


def _cfg_int(cfg: dict, key: str, default: int) -> int:
    v = cfg.get(key, "")
    v = v.strip() if isinstance(v, str) else v
    return int(v) if v not in ("", None) else default


def _cfg_float(cfg: dict, key: str, default: float) -> float:
    v = cfg.get(key, "")
    v = v.strip() if isinstance(v, str) else v
    return float(v) if v not in ("", None) else default


def _pick(cli_value, cfg_value):
    """CLI перекрывает .env — но 0 это значение, а не «не задано»."""
    return cfg_value if cli_value is None else cli_value


def render_transcript_section(segments, error: str | None, note: str) -> str:
    if error:
        return ("## Transcript\n\n"
                f"**ОШИБКА: транскрипт не получен — {error}**\n\n"
                "Текст — ядро конспекта. Не собирай конспект по одним кадрам: "
                "почини Whisper-бэкенд (`setup.py --check`) и прогони заново.")
    if segments:
        return T.format_transcript(segments)
    return f"## Transcript\n\n{note}"


def render_report(source, title, duration, reps, segments, work, focus=None,
                  intent="", transcript_error=None, transcript_note="") -> str:
    lines = ["", "# glaza: подготовка к конспекту", "",
             f"- **Source:** {source}", f"- **Title:** {title}",
             f"- **Duration:** {F.format_time(duration)} ({duration:.1f}s)"]
    if intent:
        lines.append(f"- **Intent:** {intent}")
    if focus:
        lines.append(f"- **Focus:** {F.format_time(focus[0])} → {F.format_time(focus[1])}")
    lines.append(f"- **Уникальных кадров после дедупа:** {len(reps)}")
    lines += ["", "## Уникальные кадры (представители)", "",
              "**Сам эти кадры не открывай** — раздай пачками субагентам (Step 3 SKILL.md).",
              "`t` — момент появления состояния, `span_end` — его последний кадр "
              "(его и переизвлекает `extract_one` в Step 6). Секунды в скобках — для скриптов.", ""]
    lines += [F.format_rep(r) for r in reps]
    lines.append("")
    lines.append(render_transcript_section(segments, transcript_error, transcript_note))
    lines += ["", "---", f"_Work dir: `{work}` — delete when done (см. Step 7 SKILL.md)._"]
    return "\n".join(lines)


def main() -> int:
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="glaza")
    ap.add_argument("source")
    ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--fps", type=float, default=None, help="кадров в секунду (плотность нарезки)")
    ap.add_argument("--resolution", type=int, default=None)
    ap.add_argument("--dedup-threshold", type=int, default=None, help="порог pHash (0..64)")
    ap.add_argument("--whisper", default=None)
    ap.add_argument("--lang", default=None)
    ap.add_argument("--no-transcribe", action="store_true")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--intent", default="")
    args = ap.parse_args()
    cfg = read_env()

    start = F.parse_time(args.start)
    end = F.parse_time(args.end)
    focused = start is not None or end is not None
    width = _pick(args.resolution, _cfg_int(cfg, "WATCH_RES_OVERVIEW", 1568))
    fps = _pick(args.fps, _cfg_float(cfg, "WATCH_FPS", 1.0))
    threshold = _pick(args.dedup_threshold, _cfg_int(cfg, "WATCH_DEDUP_THRESHOLD", 10))
    if fps <= 0:
        raise SystemExit(f"--fps must be > 0 (got {fps})")
    if width <= 0:
        raise SystemExit(f"--resolution must be > 0 (got {width})")
    if not 0 <= threshold <= 64:
        raise SystemExit(f"--dedup-threshold must be 0..64 (got {threshold})")

    work = Path(args.out_dir).expanduser().resolve() if args.out_dir else Path(tempfile.mkdtemp(prefix="glaza-"))
    work.mkdir(parents=True, exist_ok=True)
    print(f"[glaza] work dir: {work}", file=sys.stderr)

    dl = fetch_source(args.source, work / "download")
    video = dl["video_path"]
    meta = F.get_metadata(video)
    duration = dl["duration"] or meta["duration_seconds"]

    print(f"[glaza] extracting frames (fps={fps})…", file=sys.stderr)
    raw = F.extract_fps1(video, work / "frames", width=width, fps=fps, start=start, end=end)
    reps = DD.dedup_sequential(raw, threshold=threshold)
    print(f"[glaza] {len(raw)} кадров → {len(reps)} уникальных (dedup, порог {threshold})", file=sys.stderr)

    segments, transcript_error, transcript_note = [], None, ""
    if args.no_transcribe:
        transcript_note = "_пропущено (--no-transcribe)_"
    elif not meta["has_audio"]:
        transcript_note = "_в видео нет аудиодорожки_"
    else:
        backend = args.whisper or cfg.get("WATCH_WHISPER", "faster")
        lang = args.lang or cfg.get("WATCH_LANG", "ru")
        print(f"[glaza] transcribing ({backend}, lang={lang})…", file=sys.stderr)
        try:
            audio = T.extract_audio(video, work / "audio.wav")
            segments = T.transcribe(str(audio), lang, backend, cfg)
            if focused:
                segments = [s for s in segments if (start or 0) <= s["start"] <= (end or duration)]
        except SystemExit as exc:
            transcript_error = str(exc)
            print(f"[glaza] transcript FAILED: {exc}", file=sys.stderr)

    focus = (start or 0.0, end or duration) if focused else None
    print(render_report(args.source, dl["title"], duration, reps, segments, work, focus,
                        intent=args.intent, transcript_error=transcript_error,
                        transcript_note=transcript_note))
    return EXIT_NO_TRANSCRIPT if transcript_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
