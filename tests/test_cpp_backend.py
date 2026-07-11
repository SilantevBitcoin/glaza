import unittest, json
from pathlib import Path
import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _cpp_backend

class TestCpp(unittest.TestCase):
    def test_parse_cpp_json(self):
        payload = json.dumps({"transcription": [
            {"offsets": {"from": 0, "to": 1500}, "text": " hello"}]})
        segs = _cpp_backend.parse_cpp_json(payload)
        self.assertEqual(segs[0]["start"], 0.0)
        self.assertEqual(segs[0]["end"], 1.5)
        self.assertEqual(segs[0]["text"], "hello")

    def test_argv(self):
        cfg = {"WHISPERCPP_BIN": "whisper-cli", "WHISPERCPP_MODEL": "ggml.bin"}
        argv = _cpp_backend.build_cpp_argv(cfg, "a.wav", "en")
        self.assertIn("whisper-cli", argv); self.assertIn("ggml.bin", argv)
        self.assertTrue(any("a.wav" in str(e) for e in argv)); self.assertIn("en", argv)

if __name__ == "__main__":
    unittest.main()
