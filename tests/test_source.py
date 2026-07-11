import unittest
from pathlib import Path
import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from source import is_url, build_ytdlp_argv

class TestSource(unittest.TestCase):
    def test_is_url(self):
        self.assertTrue(is_url("https://youtu.be/x"))
        self.assertTrue(is_url("http://example.com/v.mp4"))
        self.assertFalse(is_url("-rf"))            # dash-prefixed → not a URL
        self.assertFalse(is_url("C:/videos/a.mp4"))
        self.assertFalse(is_url("./local.mkv"))
        self.assertFalse(is_url("ftp://x/y"))

    def test_argv_has_double_dash_before_url(self):
        argv = build_ytdlp_argv("https://youtu.be/x", "out.%(ext)s")
        self.assertIn("--", argv)
        self.assertEqual(argv[-1], "https://youtu.be/x")
        self.assertLess(argv.index("--"), argv.index("https://youtu.be/x"))

if __name__ == "__main__":
    unittest.main()
