"""Детект железа для install.py: что за GPU, сколько памяти.

Только факты о машине — решение «какой бэкенд» принимает install.plan().
Отсутствие инструмента (нет nvidia-smi) — это факт «нет такого GPU», а не ошибка:
установщик обязан доработать до конца на любой машине.
"""
from __future__ import annotations
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Hardware:
    os: str            # Windows | Linux | Darwin
    arch: str          # AMD64 | x86_64 | arm64
    gpu_vendor: str    # nvidia | intel_arc | amd | apple | none
    gpu_name: str      # "" если GPU не опознан
    vram_gb: float     # 0.0 = неизвестно
    ram_gb: float      # 0.0 = неизвестно


def _run(argv: list[str]) -> str | None:
    if shutil.which(argv[0]) is None:
        return None
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _parse_nvidia_smi(text: str) -> tuple[str, float] | None:
    for line in (text or "").splitlines():
        m = re.match(r"^(.+?),\s*(\d+)\s*MiB\s*$", line.strip())
        if m:
            return m.group(1).strip(), round(int(m.group(2)) / 1024, 2)
    return None


def _nvidia() -> tuple[str, float] | None:
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits"])
    if out is None:
        return None
    # nounits даёт "RTX 4070, 8188" — возвращаем суффикс, который ждёт парсер
    lines = [f"{l.rsplit(',', 1)[0].strip()}, {l.rsplit(',', 1)[-1].strip()} MiB"
             for l in out.splitlines() if "," in l]
    return _parse_nvidia_smi("\n".join(lines))


def _gpu_names() -> list[str]:
    if platform.system() == "Windows":
        out = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_VideoController).Name"])
    else:
        out = _run(["lspci"])
    return [l.strip() for l in (out or "").splitlines() if l.strip()]


def _ram_gb() -> float:
    try:
        import psutil  # не в зависимостях — берём, если человек его уже имеет
        return round(psutil.virtual_memory().total / 1024 ** 3, 1)
    except Exception:
        pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:      # Linux
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 ** 2, 1)
    except OSError:
        pass
    out = _run(["sysctl", "-n", "hw.memsize"])                  # macOS
    if out and out.strip().isdigit():
        return round(int(out.strip()) / 1024 ** 3, 1)
    if platform.system() == "Windows":
        out = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"])
        if out and out.strip().isdigit():
            return round(int(out.strip()) / 1024 ** 3, 1)
    return 0.0


def detect() -> Hardware:
    system, arch = platform.system(), platform.machine()
    ram = _ram_gb()

    nv = _nvidia()
    if nv:
        return Hardware(system, arch, "nvidia", nv[0], nv[1], ram)

    if system == "Darwin" and arch in ("arm64", "aarch64"):
        return Hardware(system, arch, "apple", "Apple Silicon", 0.0, ram)

    names = " | ".join(_gpu_names())
    low = names.lower()
    if "arc" in low or ("intel" in low and "graphics" in low):
        return Hardware(system, arch, "intel_arc", names, 0.0, ram)
    if "radeon" in low or "amd" in low:
        return Hardware(system, arch, "amd", names, 0.0, ram)
    return Hardware(system, arch, "none", names, 0.0, ram)


if __name__ == "__main__":
    import sys
    for _s in (sys.stdout, sys.stderr):     # грабля №4: cp1251-консоль
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    print(detect())
