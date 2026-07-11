import unittest, json, tempfile
from pathlib import Path
from unittest import mock
import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _ov_backend

class TestOv(unittest.TestCase):
    def test_argv_uses_config_and_outpath(self):
        cfg = {"OV_PYTHON": "py.exe", "OV_MODEL": "m", "OV_DEVICE": "GPU"}
        argv = _ov_backend.build_ov_argv(cfg, "a.wav", "en", "out.ov.json")
        self.assertEqual(argv[0], "py.exe")
        self.assertIn("m", argv); self.assertIn("en", argv); self.assertIn("GPU", argv)
        self.assertIn("out.ov.json", argv)

    def test_transcribe_reads_json_file_ignoring_stdout_noise(self):
        cfg = {"OV_PYTHON": "py.exe", "OV_MODEL": "m", "OV_DEVICE": "GPU"}
        with tempfile.TemporaryDirectory() as d:
            audio = str(Path(d) / "a.wav"); Path(audio).write_bytes(b"x")
            def fake_run(argv, **kw):
                Path(argv[-1]).write_text(json.dumps([{"start": 0, "end": 1, "text": "hi"}]), encoding="utf-8")
                return mock.Mock(returncode=0, stdout="[{...}]onednn_verbose,noise", stderr="")
            with mock.patch.object(_ov_backend.subprocess, "run", side_effect=fake_run):
                segs = _ov_backend.transcribe_ov(audio, "en", cfg)
            self.assertEqual(segs[0]["text"], "hi")   # parsed from FILE, not the noisy stdout

if __name__ == "__main__":
    unittest.main()
