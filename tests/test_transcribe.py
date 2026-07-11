import unittest
from pathlib import Path
from unittest import mock
import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import transcribe


class EnsureCudaLibs(unittest.TestCase):
    """pip кладёт cuBLAS/cuDNN в site-packages/nvidia/*/bin, а CTranslate2 ищет их
    в системных путях. На Windows Python 3.8+ не берёт DLL из PATH — нужен
    add_dll_directory, иначе 'Could not locate cudnn_ops64_9.dll'."""

    def test_no_nvidia_packages_is_silent(self):
        with mock.patch.object(transcribe, "_nvidia_lib_dirs", return_value=[]):
            transcribe._ensure_cuda_libs()      # CPU-путь не должен падать

    def test_windows_registers_dll_directory(self):
        seen = []
        with mock.patch.object(transcribe.platform, "system", return_value="Windows"), \
             mock.patch.object(transcribe, "_nvidia_lib_dirs", return_value=[r"C:\x\cudnn\bin"]), \
             mock.patch.object(transcribe.os, "add_dll_directory", create=True,
                               side_effect=lambda p: seen.append(p)):
            transcribe._ensure_cuda_libs()
        self.assertEqual(seen, [r"C:\x\cudnn\bin"])

    def test_broken_dll_dir_does_not_crash_transcription(self):
        with mock.patch.object(transcribe.platform, "system", return_value="Windows"), \
             mock.patch.object(transcribe, "_nvidia_lib_dirs", return_value=[r"C:\gone"]), \
             mock.patch.object(transcribe.os, "add_dll_directory", create=True,
                               side_effect=OSError("no such directory")):
            transcribe._ensure_cuda_libs()      # не бросает


class TestTranscribe(unittest.TestCase):
    def test_unknown_backend_raises(self):
        with self.assertRaises(SystemExit):
            transcribe.transcribe("a.wav", "en", "bogus", {})

    def test_format_transcript(self):
        segs = [{"start": 0.0, "end": 2.0, "text": "hi"},
                {"start": 2.0, "end": 4.0, "text": "there"}]
        out = transcribe.format_transcript(segs)
        self.assertIn("hi", out)
        self.assertIn("00:00", out)

if __name__ == "__main__":
    unittest.main()
