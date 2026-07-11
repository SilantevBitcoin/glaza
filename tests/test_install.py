"""Матрица установки: железо -> бэкенд/модель/пакеты. Чистая функция, GPU не нужен."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import install
from hardware import Hardware


def hw(vendor="none", vram=0.0, ram=16.0, os_="Linux", arch="x86_64"):
    return Hardware(os_, arch, vendor, "test-gpu", vram, ram)


class PlanMatrix(unittest.TestCase):
    def test_nvidia_big_vram_large_v3(self):
        p = install.plan(hw("nvidia", vram=12.0))
        self.assertEqual((p.backend, p.model), ("faster", "large-v3"))

    def test_nvidia_mid_vram_medium(self):
        self.assertEqual(install.plan(hw("nvidia", vram=6.0)).model, "medium")

    def test_nvidia_small_vram_small(self):
        self.assertEqual(install.plan(hw("nvidia", vram=3.9)).model, "small")

    def test_vram_boundaries(self):
        self.assertEqual(install.plan(hw("nvidia", vram=8.0)).model, "large-v3")
        self.assertEqual(install.plan(hw("nvidia", vram=7.99)).model, "medium")
        self.assertEqual(install.plan(hw("nvidia", vram=4.0)).model, "medium")

    def test_nvidia_gets_cuda_libs_and_explicit_device(self):
        p = install.plan(hw("nvidia", vram=12.0))
        self.assertIn("nvidia-cudnn-cu12", p.pip_packages)
        self.assertIn("nvidia-cublas-cu12", p.pip_packages)
        # FW_DEVICE=auto ищет CUDA через torch, а torch мы не ставим:
        # без явного cuda бэкенд молча уехал бы на CPU.
        self.assertEqual(p.env_values["FW_DEVICE"], "cuda")
        self.assertEqual(p.env_values["FW_COMPUTE"], "float16")

    def test_arc_openvino(self):
        p = install.plan(hw("intel_arc"))
        self.assertEqual(p.backend, "ov")
        self.assertIn("openvino-genai", p.pip_packages)
        self.assertIn("librosa", p.pip_packages)      # нужен whisper_ov_worker.py
        self.assertEqual(p.hf_repo, "OpenVINO/whisper-large-v3-fp16-ov")
        self.assertEqual(p.env_values["OV_DEVICE"], "GPU")
        self.assertEqual(p.env_values["OV_PYTHON"], sys.executable)

    def test_apple_whispercpp_and_manual_brew(self):
        p = install.plan(hw("apple", ram=32.0, os_="Darwin", arch="arm64"))
        self.assertEqual(p.backend, "whispercpp")
        self.assertEqual(p.hf_file, "ggml-large-v3.bin")
        self.assertTrue(any("brew install whisper-cpp" in s for s in p.manual_steps))

    def test_apple_low_ram_smaller_model(self):
        p = install.plan(hw("apple", ram=8.0, os_="Darwin", arch="arm64"))
        self.assertEqual((p.model, p.hf_file), ("medium", "ggml-medium.bin"))

    def test_amd_gets_build_instruction_not_brew(self):
        p = install.plan(hw("amd", ram=16.0))
        self.assertEqual(p.backend, "whispercpp")
        self.assertFalse(any("brew" in s for s in p.manual_steps))

    def test_cpu_only_int8(self):
        p = install.plan(hw("none", ram=16.0))
        self.assertEqual((p.backend, p.model), ("faster", "small"))
        self.assertEqual(p.env_values["FW_COMPUTE"], "int8")
        self.assertEqual(p.env_values["FW_DEVICE"], "cpu")
        self.assertTrue(p.warnings, "CPU-путь обязан предупредить, что это медленно")

    def test_cpu_low_ram_base(self):
        self.assertEqual(install.plan(hw("none", ram=6.0)).model, "base")

    def test_lang_ru_is_default_everywhere(self):
        for v in ("nvidia", "intel_arc", "apple", "amd", "none"):
            self.assertEqual(install.plan(hw(v, vram=12.0)).env_values["WATCH_LANG"], "ru")

    def test_every_plan_installs_pillow_and_ytdlp(self):
        for v in ("nvidia", "intel_arc", "apple", "amd", "none"):
            pkgs = install.plan(hw(v, vram=12.0)).pip_packages
            self.assertIn("Pillow", pkgs)      # без него дедуп падает уже после закачки
            self.assertIn("yt-dlp", pkgs)


class Apply(unittest.TestCase):
    def test_dry_run_touches_nothing(self):
        p = install.plan(hw("none", ram=16.0))
        with mock.patch.object(install, "_pip_install",
                               side_effect=AssertionError("dry-run не ставит пакеты")), \
             mock.patch.object(install, "write_env",
                               side_effect=AssertionError("dry-run не пишет .env")), \
             mock.patch.object(install, "_check",
                               side_effect=AssertionError("dry-run не зовёт preflight")):
            rc = install.apply(p, dry_run=True, force=False)
        self.assertEqual(rc, 0)

    def test_existing_env_is_not_overwritten_without_force(self):
        p = install.plan(hw("none", ram=16.0))
        with tempfile.TemporaryDirectory() as d:
            envp = Path(d) / ".env"
            envp.write_text("WATCH_WHISPER=ov\n", encoding="utf-8")
            with mock.patch.object(install, "ENV_PATH", envp), \
                 mock.patch.object(install.shutil, "which", return_value="/usr/bin/ffmpeg"), \
                 mock.patch.object(install, "_pip_install", return_value=0), \
                 mock.patch.object(install, "_check", return_value=0):
                install.apply(p, dry_run=False, force=False)
            self.assertEqual(envp.read_text(encoding="utf-8").strip(), "WATCH_WHISPER=ov")

    def test_force_overwrites_env(self):
        p = install.plan(hw("none", ram=16.0))
        with tempfile.TemporaryDirectory() as d:
            envp = Path(d) / ".env"
            envp.write_text("WATCH_WHISPER=ov\n", encoding="utf-8")
            with mock.patch.object(install, "ENV_PATH", envp), \
                 mock.patch.object(install.shutil, "which", return_value="/usr/bin/ffmpeg"), \
                 mock.patch.object(install, "_pip_install", return_value=0), \
                 mock.patch.object(install, "_check", return_value=0):
                install.apply(p, dry_run=False, force=True)
            self.assertIn("WATCH_WHISPER=faster", envp.read_text(encoding="utf-8"))

    def test_missing_ffmpeg_stops_before_pip(self):
        """ffmpeg нужен всему пайплайну — ставить пакеты без него бессмысленно."""
        p = install.plan(hw("none", ram=16.0))
        with mock.patch.object(install.shutil, "which", return_value=None), \
             mock.patch.object(install, "_pip_install",
                               side_effect=AssertionError("без ffmpeg до pip доходить нельзя")):
            rc = install.apply(p, dry_run=False, force=False)
        self.assertEqual(rc, 2)

    def test_pip_failure_is_loud(self):
        p = install.plan(hw("none", ram=16.0))
        with mock.patch.object(install.shutil, "which", return_value="/usr/bin/ffmpeg"), \
             mock.patch.object(install, "_pip_install", return_value=1), \
             mock.patch.object(install, "write_env",
                               side_effect=AssertionError(".env не пишется, если pip упал")):
            rc = install.apply(p, dry_run=False, force=False)
        self.assertEqual(rc, 3)

    def test_whispercpp_without_binary_fails_loudly(self):
        """Модель скачали, а бинаря нет — это провал установки, а не «почти получилось»."""
        p = install.plan(hw("apple", ram=32.0, os_="Darwin", arch="arm64"))
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(install, "ENV_PATH", Path(d) / ".env"), \
                 mock.patch.object(install.shutil, "which",
                                   side_effect=lambda b: "/usr/bin/ffmpeg" if b == "ffmpeg" else None), \
                 mock.patch.object(install, "_pip_install", return_value=0), \
                 mock.patch.object(install, "_fetch_model", return_value="/models/ggml-large-v3.bin"):
                rc = install.apply(p, dry_run=False, force=False)
        self.assertEqual(rc, 3)

    def test_ov_model_path_lands_in_env(self):
        p = install.plan(hw("intel_arc"))
        with tempfile.TemporaryDirectory() as d:
            envp = Path(d) / ".env"
            with mock.patch.object(install, "ENV_PATH", envp), \
                 mock.patch.object(install.shutil, "which", return_value="/usr/bin/ffmpeg"), \
                 mock.patch.object(install, "_pip_install", return_value=0), \
                 mock.patch.object(install, "_fetch_model", return_value="/models/whisper-ov"), \
                 mock.patch.object(install, "_check", return_value=0):
                install.apply(p, dry_run=False, force=True)
            self.assertIn("OV_MODEL=/models/whisper-ov", envp.read_text(encoding="utf-8"))


class Describe(unittest.TestCase):
    def test_shows_backend_model_and_packages(self):
        h = hw("nvidia", vram=12.0)
        text = install.describe(h, install.plan(h))
        self.assertIn("faster", text)
        self.assertIn("large-v3", text)
        self.assertIn("nvidia-cudnn-cu12", text)

    def test_shows_which_interpreter_gets_the_packages(self):
        h = hw("nvidia", vram=12.0)
        self.assertIn(sys.executable, install.describe(h, install.plan(h)))


class InterpreterWarning(unittest.TestCase):
    """Поймано живым прогоном: активный venv соседнего проекта молча получил бы
    openvino и OV_PYTHON на свой python. Человек обязан это увидеть."""

    def test_foreign_venv_is_flagged(self):
        with mock.patch.object(install.sys, "executable", r"D:\other-project\.venv\Scripts\python.exe"), \
             mock.patch.object(install.sys, "prefix", r"D:\other-project\.venv"), \
             mock.patch.object(install.sys, "base_prefix", r"C:\Python314"):
            warn = install._interpreter_warning()
        self.assertIsNotNone(warn)
        self.assertIn("ЧУЖОГО", warn)

    def test_system_python_is_flagged(self):
        with mock.patch.object(install.sys, "prefix", r"C:\Python314"), \
             mock.patch.object(install.sys, "base_prefix", r"C:\Python314"):
            self.assertIn("не virtualenv", install._interpreter_warning())

    def test_venv_inside_the_repo_is_fine(self):
        exe = install.ROOT / ".venv" / "Scripts" / "python.exe"
        with mock.patch.object(install.sys, "executable", str(exe)), \
             mock.patch.object(install.sys, "prefix", str(install.ROOT / ".venv")), \
             mock.patch.object(install.sys, "base_prefix", r"C:\Python314"):
            self.assertIsNone(install._interpreter_warning())


if __name__ == "__main__":
    unittest.main()
