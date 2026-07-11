"""Установка glaza под железо этой машины: детект -> план -> подтверждение -> применение.

Ядро (`plan`) — чистая функция: вся матрица решений тестируется без единого GPU.
Побочные эффекты (pip, скачивание модели, запись .env) живут в `apply`.

    python scripts/install.py            # спросит перед установкой
    python scripts/install.py --dry-run  # только показать план
    python scripts/install.py --yes      # без вопросов
    python scripts/install.py --force    # перезаписать существующий .env
"""
from __future__ import annotations
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from env import write_env
from hardware import Hardware, detect

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

BASE_PIP = ("Pillow", "yt-dlp")          # pHash-дедуп кадров + скачивание по ссылке
OV_MODEL_REPO = "OpenVINO/whisper-large-v3-fp16-ov"   # готовая IR — конвертация не нужна
CPP_MODEL_REPO = "ggerganov/whisper.cpp"

FFMPEG_HINT = {"Windows": "winget install Gyan.FFmpeg",
               "Darwin": "brew install ffmpeg",
               "Linux": "sudo apt install ffmpeg"}


@dataclass(frozen=True)
class Plan:
    backend: str                              # faster | ov | whispercpp
    model: str                                # large-v3 | medium | small | base
    pip_packages: tuple[str, ...]
    env_values: dict[str, str]
    hf_repo: str | None = None                # что скачать с Hugging Face
    hf_file: str | None = None                # whispercpp: конкретный ggml-файл
    manual_steps: tuple[str, ...] = ()        # то, что скрипт НЕ делает за человека
    warnings: tuple[str, ...] = ()


def _model_by_ram(ram_gb: float) -> str:
    if ram_gb >= 16:
        return "large-v3"
    if ram_gb >= 8:
        return "medium"
    return "small"


def _common_env() -> dict[str, str]:
    # ru — дефолт: на русской речи en выворачивает смысл наизнанку (грабля №6)
    return {"WATCH_LANG": "ru", "WATCH_FPS": "1",
            "WATCH_RES_OVERVIEW": "1568", "WATCH_DEDUP_THRESHOLD": "10"}


def plan(hw: Hardware) -> Plan:
    if hw.gpu_vendor == "nvidia":
        model = "large-v3" if hw.vram_gb >= 8 else ("medium" if hw.vram_gb >= 4 else "small")
        return Plan(
            backend="faster", model=model,
            pip_packages=BASE_PIP + ("faster-whisper", "nvidia-cublas-cu12", "nvidia-cudnn-cu12"),
            # FW_DEVICE=auto определяет CUDA через `import torch`, а torch мы не ставим (2+ ГБ).
            # Без явного cuda транскрипция молча уехала бы на CPU.
            env_values={**_common_env(), "WATCH_WHISPER": "faster", "FW_MODEL": model,
                        "FW_DEVICE": "cuda", "FW_COMPUTE": "float16"},
            warnings=("CUDA-библиотеки ставятся pip-пакетами (cuBLAS + cuDNN 9); "
                      "нужен драйвер NVIDIA с поддержкой CUDA 12.",))

    if hw.gpu_vendor == "intel_arc":
        return Plan(
            backend="ov", model="large-v3",
            pip_packages=BASE_PIP + ("openvino-genai", "librosa", "soundfile", "huggingface_hub"),
            # OV_MODEL допишет apply() — путь становится известен после скачивания
            env_values={**_common_env(), "WATCH_WHISPER": "ov",
                        "OV_PYTHON": sys.executable, "OV_DEVICE": "GPU"},
            hf_repo=OV_MODEL_REPO)

    if hw.gpu_vendor in ("apple", "amd"):
        model = _model_by_ram(hw.ram_gb)
        install_cmd = ("brew install whisper-cpp" if hw.os == "Darwin" else
                       "собери whisper.cpp с Vulkan: https://github.com/ggml-org/whisper.cpp")
        return Plan(
            backend="whispercpp", model=model,
            pip_packages=BASE_PIP + ("huggingface_hub",),
            # WHISPERCPP_BIN / WHISPERCPP_MODEL допишет apply()
            env_values={**_common_env(), "WATCH_WHISPER": "whispercpp"},
            hf_repo=CPP_MODEL_REPO, hf_file=f"ggml-{model}.bin",
            manual_steps=(f"Поставь бинарь whisper-cli: {install_cmd}",),
            warnings=("whisper.cpp не ставится через pip — нужен пакетный менеджер или сборка.",))

    model = "small" if hw.ram_gb >= 8 else "base"
    return Plan(
        backend="faster", model=model,
        pip_packages=BASE_PIP + ("faster-whisper",),
        env_values={**_common_env(), "WATCH_WHISPER": "faster", "FW_MODEL": model,
                    "FW_DEVICE": "cpu", "FW_COMPUTE": "int8"},
        warnings=("GPU не найден — транскрипция пойдёт на CPU. Это работает, но медленно.",))


def _pip_install(packages: tuple[str, ...]) -> int:
    return subprocess.run([sys.executable, "-m", "pip", "install", "-U", *packages]).returncode


def _check() -> int:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "setup.py"), "--check"]).returncode


def _fetch_model(p: Plan) -> str:
    from huggingface_hub import hf_hub_download, snapshot_download
    if p.hf_file:
        return hf_hub_download(repo_id=p.hf_repo, filename=p.hf_file)
    return snapshot_download(repo_id=p.hf_repo)


def _interpreter_warning() -> str | None:
    """Пакеты идут в sys.executable. Если это venv чужого проекта — человек должен знать.

    Поймано живым прогоном: install.py, запущенный из-под venv соседнего проекта,
    молча прописал бы OV_PYTHON на чужой интерпретатор и залил туда openvino.
    """
    exe = Path(sys.executable).resolve()
    if sys.prefix == sys.base_prefix:
        return f"Это не virtualenv — пакеты поставятся в системный Python ({exe})."
    try:
        exe.relative_to(ROOT)
    except ValueError:
        return (f"Активен virtualenv ЧУЖОГО проекта ({exe}).\n"
                f"            Пакеты и OV_PYTHON уйдут туда. Если это не то, чего ты хочешь — "
                f"активируй venv glaza и запусти снова.")
    return None


def describe(hw: Hardware, p: Plan) -> str:
    vram = f", {hw.vram_gb} ГБ VRAM" if hw.vram_gb else ""
    lines = [
        f"  Железо:   {hw.os}/{hw.arch} · GPU: {hw.gpu_name or 'не найден'} ({hw.gpu_vendor}{vram}) · RAM {hw.ram_gb} ГБ",
        f"  Бэкенд:   {p.backend}, модель {p.model}",
        f"  Python:   {sys.executable}",
        f"  Поставлю: {', '.join(p.pip_packages)}",
    ]
    if p.hf_repo:
        lines.append(f"  Скачаю:   {p.hf_repo}{'/' + p.hf_file if p.hf_file else ''}")
    lines.append(f"  Запишу:   {ENV_PATH}")
    lines += [f"            {k}={v}" for k, v in p.env_values.items()]
    lines += [f"  ! {w}" for w in p.warnings]
    warn = _interpreter_warning()
    if warn:
        lines.append(f"  ! {warn}")
    lines += [f"  Руками:   {s}" for s in p.manual_steps]
    return "\n".join(lines)


def apply(p: Plan, *, dry_run: bool, force: bool) -> int:
    """0 — готово; 1 — отменено; 2 — нет ffmpeg; 3 — бэкенд не встал."""
    if dry_run:
        print("\n[dry-run] ничего не установлено, .env не тронут")
        return 0

    if shutil.which("ffmpeg") is None:
        hint = FFMPEG_HINT.get(platform.system(), "см. https://ffmpeg.org/download.html")
        print(f"\nffmpeg не найден — без него не работает ни нарезка кадров, ни звук.\n"
              f"Поставь его сам:  {hint}\nПотом запусти install.py снова.")
        return 2

    if _pip_install(p.pip_packages) != 0:
        print("\npip install не удался — установка прервана, .env не тронут")
        return 3

    env_values = dict(p.env_values)

    if p.hf_repo:
        print(f"\nСкачиваю модель {p.hf_repo}…")
        try:
            path = _fetch_model(p)
        except Exception as exc:                       # сеть/HF/диск — причина нужна словами
            print(f"не удалось скачать модель: {exc}")
            return 3
        env_values["OV_MODEL" if p.backend == "ov" else "WHISPERCPP_MODEL"] = path

    if p.backend == "whispercpp":
        binary = shutil.which("whisper-cli") or shutil.which("whisper-cpp")
        if not binary:
            print("\nwhisper-cli не найден в PATH — модель скачана, но запускать её нечем.\n"
                  + "\n".join(f"  {s}" for s in p.manual_steps)
                  + "\nПотом запусти install.py снова.")
            return 3
        env_values["WHISPERCPP_BIN"] = binary

    if ENV_PATH.exists() and not force:
        print(f"\n.env уже существует — не трогаю его. Нужные значения (или запусти с --force):")
        for k, v in env_values.items():
            print(f"  {k}={v}")
    else:
        write_env(ENV_PATH, env_values)
        print(f"\n.env записан: {ENV_PATH}")

    rc = _check()
    print("готово — preflight зелёный, можно звать /glaza" if rc == 0
          else f"preflight не прошёл (код {rc}) — см. сообщение выше")
    return rc


def main(argv: list[str]) -> int:
    dry_run, force, yes = "--dry-run" in argv, "--force" in argv, "--yes" in argv
    hw = detect()
    p = plan(hw)
    print("glaza — установка под это железо:\n" + describe(hw, p))

    if not (yes or dry_run):
        try:
            answer = input("\nСтавим? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes", "д", "да"):
            print("отменено")
            return 1
    return apply(p, dry_run=dry_run, force=force)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):     # грабля №4: cp1251-консоль роняет UnicodeEncodeError
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
