import os
import tempfile
import unittest
from pathlib import Path
import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from env import read_env, write_env


class TestEnv(unittest.TestCase):
    def test_parses_and_strips_quotes_and_comment_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text('# c\nWATCH_WHISPER=faster\nFW_MODEL="large-v3"\n\n', encoding="utf-8")
            cfg = read_env([p])
            self.assertEqual(cfg["WATCH_WHISPER"], "faster")
            self.assertEqual(cfg["FW_MODEL"], "large-v3")
            self.assertNotIn("", cfg)

    def test_strips_inline_comment(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("WATCH_RES_OVERVIEW=1568  # ширина кадра\n", encoding="utf-8")
            self.assertEqual(read_env([p])["WATCH_RES_OVERVIEW"], "1568")

    def test_keeps_hash_without_leading_space(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("OV_MODEL=C:/models/whisper#3\n", encoding="utf-8")
            self.assertEqual(read_env([p])["OV_MODEL"], "C:/models/whisper#3")

    def test_os_env_overrides_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"; p.write_text("WATCH_LANG=ru\n", encoding="utf-8")
            os.environ["WATCH_LANG"] = "en"
            try:
                self.assertEqual(read_env([p])["WATCH_LANG"], "en")
            finally:
                del os.environ["WATCH_LANG"]

    def test_bom_prefixed_env_parses_first_key(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_bytes(b"\xef\xbb\xbfWATCH_WHISPER=faster\nFW_MODEL=large-v3\n")
            cfg = read_env([p])
            self.assertEqual(cfg["WATCH_WHISPER"], "faster")
            self.assertEqual(cfg["FW_MODEL"], "large-v3")

    def test_does_not_read_cwd_env(self):
        """Скилл запускают из чужого проекта — его .env не должен перебивать наш."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / ".env").write_text("WATCH_WHISPER=hijacked\n", encoding="utf-8")
            cwd = os.getcwd()
            os.chdir(d)
            try:
                self.assertNotEqual(read_env().get("WATCH_WHISPER"), "hijacked")
            finally:
                os.chdir(cwd)


class WriteEnv(unittest.TestCase):
    def test_creates_file_with_values(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            write_env(p, {"WATCH_WHISPER": "faster", "FW_MODEL": "small"})
            cfg = read_env([p])
            self.assertEqual(cfg["WATCH_WHISPER"], "faster")
            self.assertEqual(cfg["FW_MODEL"], "small")

    def test_updates_key_in_place_keeps_the_rest(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("# мой коммент\nWATCH_LANG=ru\nFW_MODEL=large-v3\n", encoding="utf-8")
            write_env(p, {"FW_MODEL": "medium"})
            text = p.read_text(encoding="utf-8")
            self.assertIn("# мой коммент", text)      # чужие строки не тронуты
            self.assertIn("WATCH_LANG=ru", text)
            self.assertIn("FW_MODEL=medium", text)
            self.assertNotIn("large-v3", text)        # значение заменено, а не задвоено

    def test_windows_path_value_survives(self):
        """Путь к модели содержит backslash и двоеточие — не должен покорёжиться."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            win = r"C:\models\whisper-ov\openvino_model.xml"
            write_env(p, {"OV_MODEL": win})
            self.assertEqual(read_env([p])["OV_MODEL"], win)


if __name__ == "__main__":
    unittest.main()
