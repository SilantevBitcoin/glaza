import tempfile
import unittest
from pathlib import Path
import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

if HAS_PIL:
    import dedup as D


def _paint(path: Path, fn) -> None:
    img = Image.new("L", (64, 64))
    px = img.load()
    for x in range(64):
        for y in range(64):
            px[x, y] = fn(x, y)
    img.save(path)


def _grad_h(path: Path) -> None:
    _paint(path, lambda x, y: min(255, x * 4))


def _grad_v(path: Path) -> None:
    _paint(path, lambda x, y: min(255, y * 4))


def _frames(td: Path, painters) -> list[dict]:
    out = []
    for i, painter in enumerate(painters):
        p = td / f"f{i}.png"
        painter(p)
        out.append({"index": i, "timestamp": float(i), "path": str(p)})
    return out


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class TestDhash(unittest.TestCase):
    def test_identical_and_different(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            a1, a2, b = td / "a1.png", td / "a2.png", td / "b.png"
            _grad_h(a1); _grad_h(a2); _grad_v(b)
            self.assertEqual(D.hamming(D.dhash(str(a1)), D.dhash(str(a2))), 0)
            self.assertGreater(D.hamming(D.dhash(str(a1)), D.dhash(str(b))), 10)


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class TestDedupSequential(unittest.TestCase):
    def test_contract_last_frame_is_representative(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            fr = _frames(td, [_grad_h] * 5 + [_grad_v] * 3)
            reps = D.dedup_sequential(fr, threshold=10)
            self.assertEqual(len(reps), 2)
            self.assertEqual(reps[0]["timestamp"], 0.0, "timestamp = появление серии")
            self.assertEqual(reps[0]["span_end_timestamp"], 4.0)
            self.assertTrue(reps[0]["path"].endswith("f4.png"), "представитель = последний кадр")
            self.assertEqual(reps[1]["timestamp"], 5.0)
            self.assertTrue(reps[1]["path"].endswith("f7.png"))

    def test_empty(self):
        self.assertEqual(D.dedup_sequential([]), [])

    def test_single_frame(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            reps = D.dedup_sequential(_frames(Path(td), [_grad_h]))
            self.assertEqual(len(reps), 1)
            self.assertEqual(reps[0]["span_end_timestamp"], reps[0]["timestamp"])

    def test_all_identical_collapse_to_one(self):
        with tempfile.TemporaryDirectory() as td:
            reps = D.dedup_sequential(_frames(Path(td), [_grad_h] * 5))
            self.assertEqual(len(reps), 1)
            self.assertEqual(reps[0]["index"], 4)

    def test_all_different_keep_all(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            painters = [lambda x, y, i=i: 255 if ((x // 4 + y // 4 + i) % 2) else 0
                        for i in range(5)]
            fr = _frames(td, [lambda p, f=f: _paint(p, f) for f in painters])
            self.assertEqual(len(D.dedup_sequential(fr)), 5)

    def test_slow_drift_breaks_runs_against_the_first_frame(self):
        """Каждый кадр близок к соседу, но далёк от начала серии: baseline —
        первый кадр серии, поэтому дрейф обязан рвать серию, а не тянуться вечно."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            fr = _frames(td, [lambda p, i=i: _paint(p, lambda x, y: 255 if x > i * 1.5 else 0)
                              for i in range(40)])
            reps = D.dedup_sequential(fr, threshold=10)
            self.assertGreater(len(reps), 1, "дрейф не должен схлопываться в одну серию")
            self.assertLess(len(reps), 40, "соседи всё же близки — не каждый кадр отдельная серия")
            for rep in reps:
                self.assertGreaterEqual(rep["span_end_timestamp"], rep["timestamp"])

    def test_threshold_zero_disables_dedup(self):
        with tempfile.TemporaryDirectory() as td:
            fr = _frames(Path(td), [_grad_h] * 5)
            self.assertEqual(len(D.dedup_sequential(fr, threshold=0)), 5)


if __name__ == "__main__":
    unittest.main()
