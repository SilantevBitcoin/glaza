import unittest
from pathlib import Path
from unittest import mock
import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import setup as glaza_setup


class TestSetup(unittest.TestCase):
    def test_missing_bins_detected(self):
        with mock.patch.object(glaza_setup.shutil, "which", return_value=None):
            s = glaza_setup.status({"WATCH_WHISPER": "faster"})
        self.assertIn("ffmpeg", s["missing_bins"])
        self.assertIn("ffprobe", s["missing_bins"])

    def test_ytdlp_is_optional_not_required(self):
        """Локальному файлу yt-dlp не нужен — он не должен блокировать preflight."""
        with mock.patch.object(glaza_setup.shutil, "which", return_value=None):
            s = glaza_setup.status({"WATCH_WHISPER": "faster"})
        self.assertNotIn("yt-dlp", s["missing_bins"])
        self.assertIn("yt-dlp", s["missing_optional"])

    def test_faster_backend_requires_the_package(self):
        """Preflight обязан ловить это ДО закачки видео, а не на транскрипции."""
        with mock.patch.object(glaza_setup.shutil, "which", return_value="x"), \
             mock.patch.object(glaza_setup.importlib.util, "find_spec", return_value=None):
            s = glaza_setup.status({"WATCH_WHISPER": "faster"})
        self.assertFalse(s["backend_ready"])
        self.assertIn("faster-whisper", s["backend_problem"])

    def test_faster_backend_ready_when_installed(self):
        with mock.patch.object(glaza_setup.shutil, "which", return_value="x"), \
             mock.patch.object(glaza_setup.importlib.util, "find_spec", return_value=object()):
            s = glaza_setup.status({"WATCH_WHISPER": "faster"})
        self.assertTrue(s["backend_ready"])

    def test_ov_backend_needs_paths(self):
        with mock.patch.object(glaza_setup.shutil, "which", return_value="x"):
            s = glaza_setup.status({"WATCH_WHISPER": "ov"})  # no OV_PYTHON
        self.assertFalse(s["backend_ready"])
        self.assertIn("OV_PYTHON", s["backend_problem"])

    def test_ov_backend_rejects_nonexistent_paths(self):
        cfg = {"WATCH_WHISPER": "ov", "OV_PYTHON": "X:/nope/python.exe", "OV_MODEL": "X:/nope"}
        with mock.patch.object(glaza_setup.shutil, "which", return_value="x"):
            s = glaza_setup.status(cfg)
        self.assertFalse(s["backend_ready"])
        self.assertIn("несуществующий путь", s["backend_problem"])

    def test_whispercpp_rejects_nonexistent_paths(self):
        cfg = {"WATCH_WHISPER": "whispercpp",
               "WHISPERCPP_BIN": "X:/nope/whisper-cli", "WHISPERCPP_MODEL": "X:/nope.bin"}
        with mock.patch.object(glaza_setup.shutil, "which", return_value="x"):
            s = glaza_setup.status(cfg)
        self.assertFalse(s["backend_ready"])

    def test_unknown_backend_not_ready(self):
        with mock.patch.object(glaza_setup.shutil, "which", return_value="x"):
            s = glaza_setup.status({"WATCH_WHISPER": "bogus"})
        self.assertFalse(s["backend_ready"])


if __name__ == "__main__":
    unittest.main()
