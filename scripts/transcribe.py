"""Audio extraction + local Whisper dispatch (faster | ov | whispercpp)."""
from __future__ import annotations
import ctypes, importlib.util, os, platform, shutil, subprocess, sys
from pathlib import Path


def _nvidia_lib_dirs() -> list[str]:
    """Каталоги с cuBLAS/cuDNN из pip-пакетов nvidia-cublas-cu12 / nvidia-cudnn-cu12."""
    dirs: list[str] = []
    for mod in ("nvidia.cublas", "nvidia.cudnn"):
        try:
            spec = importlib.util.find_spec(mod)
        except (ImportError, ValueError, ModuleNotFoundError):
            continue
        if not spec or not spec.submodule_search_locations:
            continue
        root = Path(list(spec.submodule_search_locations)[0])
        dirs += [str(root / sub) for sub in ("bin", "lib") if (root / sub).is_dir()]
    return dirs


def _ensure_cuda_libs() -> None:
    """CTranslate2 ищет cuBLAS/cuDNN в системных путях, а pip кладёт их в site-packages.
    Python 3.8+ на Windows не берёт DLL из PATH — нужен add_dll_directory; на Linux
    достаточно предзагрузить .so. Без этого: 'Could not locate cudnn_ops64_9.dll'.
    Нет пакетов (CPU-путь) — молча выходим."""
    for d in _nvidia_lib_dirs():
        if platform.system() == "Windows":
            try:
                os.add_dll_directory(d)
            except OSError:
                pass
        else:
            for so in sorted(Path(d).glob("lib*.so*")):
                try:
                    ctypes.CDLL(str(so), mode=getattr(ctypes, "RTLD_GLOBAL", 0))
                except OSError:
                    pass


def extract_audio(video: str, out_wav: Path) -> Path:
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg not installed.")
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(Path(video).resolve()), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(out_wav.resolve())],
        capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg audio extraction failed: {r.stderr.strip()}")
    if not out_wav.exists() or out_wav.stat().st_size == 0:
        raise SystemExit("ffmpeg produced no audio — video may have no audio track")
    return out_wav


def _norm(segments) -> list[dict]:
    out = []
    for s in segments:
        text = (s["text"] or "").strip()
        if text:
            out.append({"start": round(float(s["start"]), 2),
                        "end": round(float(s["end"]), 2), "text": text})
    return out


def _faster(audio_path: str, lang: str, cfg: dict) -> list[dict]:
    _ensure_cuda_libs()      # до импорта: CTranslate2 грузит CUDA-библиотеки при импорте
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit("faster-whisper not installed. Install: pip install faster-whisper")
    device = cfg.get("FW_DEVICE", "auto")
    if device == "auto":
        try:
            import torch  # optional
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"
    compute = cfg.get("FW_COMPUTE", "auto")
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    model = WhisperModel(cfg.get("FW_MODEL", "large-v3"), device=device, compute_type=compute)
    language = None if lang in ("", "auto") else lang
    segments, _ = model.transcribe(audio_path, language=language)
    return _norm({"start": s.start, "end": s.end, "text": s.text} for s in segments)


def transcribe(audio_path: str, lang: str, backend: str, cfg: dict) -> list[dict]:
    if backend == "faster":
        return _faster(audio_path, lang, cfg)
    if backend == "ov":
        from _ov_backend import transcribe_ov      # Task 8
        return _norm(transcribe_ov(audio_path, lang, cfg))
    if backend == "whispercpp":
        from _cpp_backend import transcribe_cpp     # Task 9
        return _norm(transcribe_cpp(audio_path, lang, cfg))
    raise SystemExit(f"Unknown whisper backend: {backend!r} (ov|faster|whispercpp)")


def format_transcript(segments) -> str:
    from frames import format_time
    lines = ["## Transcript", ""]
    for s in segments:
        lines.append(f"[{format_time(s['start'])}] {s['text']}")
    return "\n".join(lines)
