import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import frames as F

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def _make_clip(path: Path, secs: int, size: str = "320x240") -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={secs}:size={size}:rate=10",
         "-pix_fmt", "yuv420p", str(path)],
        check=True)


class TestTimeHelpers(unittest.TestCase):
    def test_format_time(self):
        self.assertEqual(F.format_time(75), "01:15")
        self.assertEqual(F.format_time(3675), "1:01:15")

    def test_parse_time(self):
        self.assertEqual(F.parse_time("2:15"), 135.0)
        self.assertEqual(F.parse_time("1:02:03"), 3723.0)
        self.assertEqual(F.parse_time(12.5), 12.5)
        self.assertIsNone(F.parse_time(None))

    def test_parse_time_rejects_garbage(self):
        with self.assertRaises(SystemExit):
            F.parse_time("nope")


@unittest.skipUnless(HAS_FFMPEG, "needs ffprobe")
class TestMetadata(unittest.TestCase):
    def test_reports_effective_fps(self):
        """fps приходит из avg_frame_rate (реальная средняя частота), не из
        r_frame_rate: последний на VFR-скринкасте рапортует номинал (25/30/1000),
        а не сколько кадров в секунду на экране реально меняется."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"
            _make_clip(clip, 3)   # testsrc rate=10
            meta = F.get_metadata(str(clip))
            self.assertAlmostEqual(meta["fps"], 10.0, delta=0.5)


@unittest.skipUnless(HAS_FFMPEG and HAS_PIL, "needs ffmpeg + Pillow")
class TestExtractFps1(unittest.TestCase):
    def test_fps_and_timestamps(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"
            _make_clip(clip, 5)
            got = F.extract_fps1(str(clip), d / "frames", width=320, fps=1.0)
            self.assertTrue(4 <= len(got) <= 6, f"~5 frames for 5s@1fps, got {len(got)}")
            self.assertEqual(got[0]["timestamp"], 0.0)
            self.assertEqual(got[1]["timestamp"], 1.0)
            self.assertTrue(all(Path(f["path"]).exists() for f in got))

    def test_never_upscales_a_small_source(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"
            _make_clip(clip, 2, size="320x240")
            got = F.extract_fps1(str(clip), d / "frames", width=1568, fps=1.0)
            self.assertEqual(Image.open(got[0]["path"]).width, 320,
                             "источник уже width — кадр обязан остаться родным")

    def test_downscales_a_large_source(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"
            _make_clip(clip, 2, size="1920x1080")
            got = F.extract_fps1(str(clip), d / "frames", width=1568, fps=1.0)
            self.assertEqual(Image.open(got[0]["path"]).width, 1568)

    def test_window_trims_the_decode_not_just_the_result(self):
        """--start/--end должны резать сам декод: иначе «вариант B» на 25 мин
        нарежет всё видео ради 30-секундного окна."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"
            _make_clip(clip, 10)
            out = d / "frames"
            got = F.extract_fps1(str(clip), out, width=320, fps=1.0, start=6.0, end=9.0)
            on_disk = list(out.glob("frame_*.jpg"))
            self.assertEqual(len(on_disk), len(got), "лишние кадры не должны попадать на диск")
            self.assertEqual(len(got), 3, "окно [6,9) при fps=1 = 3 кадра")
            self.assertEqual([f["timestamp"] for f in got], [6.0, 7.0, 8.0],
                             "timestamp = start + i/fps")


@unittest.skipUnless(HAS_FFMPEG and HAS_PIL, "needs ffmpeg + Pillow")
class TestExtractOne(unittest.TestCase):
    def test_native_and_scaled(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"
            _make_clip(clip, 5)
            native = d / "native.jpg"
            F.extract_one(str(clip), 2.0, native)
            self.assertEqual(Image.open(native).width, 320, "width=None -> родное разрешение")
            scaled = d / "scaled.jpg"
            F.extract_one(str(clip), 2.0, scaled, width=160)
            self.assertEqual(Image.open(scaled).width, 160)

    def test_cli_extract_one(self):
        """Step 6 SKILL.md вызывает именно CLI — он обязан существовать."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"
            _make_clip(clip, 3)
            out = d / "shot.jpg"
            r = subprocess.run(
                [sys.executable, str(Path(F.__file__)), "extract-one",
                 "--video", str(clip), "--ts", "0:02", "--out", str(out)],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
