import tempfile
import unittest
from pathlib import Path
import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import inline_images as II

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class TestInline(unittest.TestCase):
    def _run(self, body: str) -> str:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            Image.new("RGB", (32, 32), (200, 90, 40)).save(td / "a.jpg", "JPEG")
            html_in, html_out = td / "in.html", td / "out.html"
            html_in.write_text(f"<!doctype html><body>{body}</body>", encoding="utf-8")
            II.inline(str(html_in), str(html_out), str(td))
            return html_out.read_text(encoding="utf-8")

    def test_double_quoted_src_is_inlined(self):
        out = self._run('<p>текст</p><img src="a.jpg" alt="скрин">')
        self.assertIn("data:image/jpeg;base64,", out)
        self.assertNotIn('src="a.jpg"', out)

    def test_single_quoted_src_is_inlined(self):
        """HTML пишет модель — кавычки могут быть любые. Тихий пропуск давал
        битую картинку в «самодостаточном» файле."""
        out = self._run("<img src='a.jpg'>")
        self.assertIn("data:image/jpeg;base64,", out)
        self.assertNotIn("src='a.jpg'", out)

    def test_remote_and_data_sources_left_alone(self):
        out = self._run('<img src="https://x/y.png"><img src="data:image/png;base64,AAA">')
        self.assertIn('src="https://x/y.png"', out)
        self.assertIn('src="data:image/png;base64,AAA"', out)

    def test_missing_file_left_as_is(self):
        out = self._run('<img src="nope.jpg">')
        self.assertIn('src="nope.jpg"', out)


if __name__ == "__main__":
    unittest.main()
