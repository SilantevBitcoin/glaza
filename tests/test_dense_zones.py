import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path
import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dense_zones as DZ
import frames as F

HAS_FFMPEG = shutil.which("ffmpeg") is not None
try:
    from PIL import Image
    import dedup as DD          # dedup.py тянет Pillow на верхнем уровне
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def _ui(*timestamps) -> list[dict]:
    return [{"frame": f"f{t}.jpg", "timestamp": t, "category": "ui"} for t in timestamps]


def _head(*timestamps) -> list[dict]:
    return [{"frame": f"f{t}.jpg", "timestamp": t, "category": "talking_head"}
            for t in timestamps]


class TestDetectDenseZones(unittest.TestCase):
    def test_one_zone_padded_around_the_ui_stretch(self):
        journal = _ui(*range(10, 21)) + _head(*range(21, 41))
        self.assertEqual(DZ.detect_dense_zones(journal), [(8.0, 22.0)])

    def test_small_gap_merges(self):
        """Разрыв 3 с < gap_merge_sec=5 — это одна зона, а не две."""
        journal = _ui(*range(10, 21)) + _ui(*range(23, 31))
        self.assertEqual(DZ.detect_dense_zones(journal), [(8.0, 32.0)])

    def test_large_gap_splits(self):
        journal = _ui(*range(10, 21)) + _ui(*range(40, 51))
        self.assertEqual(DZ.detect_dense_zones(journal), [(8.0, 22.0), (38.0, 52.0)])

    def test_zone_shorter_than_min_is_dropped(self):
        """Одиночный ui-кадр — не зона. Длина меряется до расширения."""
        self.assertEqual(DZ.detect_dense_zones(_ui(12.0)), [])
        self.assertEqual(DZ.detect_dense_zones(_ui(12.0, 13.0)), [], "2 c < min_zone_sec=3")
        self.assertEqual(DZ.detect_dense_zones(_ui(12.0, 15.0)), [(10.0, 17.0)])

    def test_empty_journal(self):
        self.assertEqual(DZ.detect_dense_zones([]), [])

    def test_journal_without_dense_categories(self):
        self.assertEqual(DZ.detect_dense_zones(_head(*range(0, 30))), [])

    def test_deterministic_and_order_independent(self):
        journal = _ui(*range(10, 21)) + _ui(*range(40, 51))
        first = DZ.detect_dense_zones(journal)
        self.assertEqual(first, DZ.detect_dense_zones(journal))
        self.assertEqual(first, DZ.detect_dense_zones(list(reversed(journal))))

    def test_padding_never_goes_negative(self):
        self.assertEqual(DZ.detect_dense_zones(_ui(0.0, 1.0, 2.0, 3.0, 4.0))[0][0], 0.0)

    def test_padding_merges_zones_it_glued(self):
        """При дефолтах 2*pad=4 < gap_merge=5, и расширение сомкнуть соседей не может.
        При pad_sec=3 может — и тогда результат обязан быть одной зоной, а не двумя
        пересекающимися."""
        journal = _ui(*range(10, 16)) + _ui(20.5, 21.5, 22.5, 23.5)
        self.assertEqual(DZ.detect_dense_zones(journal), [(8.0, 17.0), (18.5, 25.5)])
        self.assertEqual(DZ.detect_dense_zones(journal, pad_sec=3.0), [(7.0, 26.5)])

    def test_broken_records_are_skipped(self):
        journal = _ui(10.0, 11.0) + [
            {"category": "ui"},                       # нет timestamp
            {"category": "ui", "timestamp": None},    # не число
            {"category": "ui", "timestamp": "13.0"},  # строка — приводится
            {"category": "ui", "timestamp": 14.0},
        ]
        self.assertEqual(DZ.detect_dense_zones(journal), [(8.0, 16.0)])

    def test_custom_categories(self):
        journal = [{"timestamp": t, "category": "slide"} for t in range(10, 21)]
        self.assertEqual(DZ.detect_dense_zones(journal), [])
        self.assertEqual(DZ.detect_dense_zones(journal, categories={"slide"}), [(8.0, 22.0)])

    def test_screen_content_rescues_misclassified_category(self):
        """Субагент назвал меняющийся экран 'slide' (ошибка классификации), но
        честно пометил has_screen_content=true → зона обязана найтись. Ошибки
        несимметричны: пропущенная зона теряет применённое состояние — ровно баг,
        ради которого фича и существует."""
        journal = [{"timestamp": float(t), "category": "slide", "has_screen_content": True}
                   for t in range(10, 21)]
        self.assertEqual(DZ.detect_dense_zones(journal), [(8.0, 22.0)])

    def test_talking_head_excluded_even_with_screen_content(self):
        """has_screen_content у говорящей головы не делает её зоной — голова и
        переход исключены явно."""
        journal = [{"timestamp": float(t), "category": "talking_head", "has_screen_content": True}
                   for t in range(10, 21)]
        self.assertEqual(DZ.detect_dense_zones(journal), [])

    def test_screen_content_false_falls_back_to_allowlist(self):
        """has_screen_content=false и категория вне allow-list → не зона (старое
        поведение сохранено для журналов без нового сигнала)."""
        journal = [{"timestamp": float(t), "category": "other", "has_screen_content": False}
                   for t in range(10, 21)]
        self.assertEqual(DZ.detect_dense_zones(journal), [])


class TestCountNew(unittest.TestCase):
    """Представители зоны часто садятся на те же моменты, что представители первого
    прохода. Считаем, сколько реально новых (в отчёте видно, что дал второй проход);
    НЕ удаляем — потеря представителя в demo-зоне дороже пары дублей."""

    def test_new_vs_journal_stamps(self):
        reps = [{"timestamp": 10.0}, {"timestamp": 10.33}, {"timestamp": 10.67}]
        # 10.0 садится на представителя первого прохода → дубль; 10.33/10.67 — новые
        # моменты, которых fps=1 не сэмплил
        self.assertEqual(DZ._count_new(reps, [10.0, 11.0]), 2)

    def test_all_new_when_no_overlap(self):
        reps = [{"timestamp": 10.33}, {"timestamp": 10.67}]
        self.assertEqual(DZ._count_new(reps, [11.0, 20.0]), 2)

    def test_empty_reps(self):
        self.assertEqual(DZ._count_new([], [10.0]), 0)


class TestJournalStats(unittest.TestCase):
    """Задача 3: битую запись сейчас тихо роняем, и опечатка в схеме выглядит как
    «зон не найдено». Считаем причины, чтобы напечатать их в stderr."""

    def test_counts_working_other_and_broken(self):
        journal = _ui(10.0, 11.0) + _head(20.0) + [
            {"category": "ui"},                        # битая: нет timestamp
            {"category": "ui", "timestamp": "bad"},    # битая: не число
        ]
        st = DZ._journal_stats(journal)
        self.assertEqual(st["total"], 5)
        self.assertEqual(st["dense"], 2, "два ui с валидным ts")
        self.assertEqual(st["other_category"], 1, "talking_head — валидна, но не demo")
        self.assertEqual(st["broken"], 2, "нет ts + нечисловой ts")

    def test_broken_wins_over_category(self):
        """Запись demo-категории, но с битым ts — идёт в broken, не в dense."""
        st = DZ._journal_stats([{"category": "ui", "timestamp": None}])
        self.assertEqual(st["broken"], 1)
        self.assertEqual(st["dense"], 0)


class TestClampFps(unittest.TestCase):
    """Sanity-guard владельца: fps зажимается в min(10, частота видео).
    Выше частоты видео растут только дубли; выше 10 кадров/с человек состояние
    короче 0.1 с всё равно не различит. Отказом не является — только clamp."""

    def test_owner_ceiling_and_video_rate(self):
        cases = [
            (300, 10.0, 10.0, "опечатка --fps 300 на 10-fps видео → потолок 10"),
            (30, 60.0, 10.0, "60-fps видео: всё равно потолок владельца 10, не 60"),
            (3, 5.0, 3.0, "ниже обоих потолков — не трогать"),
            (8, 5.0, 5.0, "частота видео (5) ниже потолка (10) — зажать до 5"),
        ]
        for req, vfps, want, msg in cases:
            with self.subTest(req=req, vfps=vfps):
                got, clamped = DZ._clamp_fps(req, vfps)
                self.assertEqual(got, want, msg)
                self.assertEqual(clamped, req != want)

    def test_unknown_video_fps_falls_back_to_ceiling_only(self):
        """ffprobe не дал частоту (0) → зажимаем только по потолку 10, видео игнор."""
        self.assertEqual(DZ._clamp_fps(300, 0.0), (10.0, True))
        self.assertEqual(DZ._clamp_fps(3, 0.0), (3.0, False))


class TestEstimate(unittest.TestCase):
    """Смета стоимости считается ДО нарезки: стоимость = покрытие(сек) × fps,
    токены = кадры × ⌈w/28⌉ × ⌈h/28⌉ (для 1568×882 это 1792)."""

    def test_frames_tokens_and_coverage(self):
        est = DZ._estimate([(10.0, 50.0)], fps=3.0, width=1568, height=882, duration=600.0)
        self.assertEqual(est["frames"], 120, "40 c × 3 fps")
        self.assertEqual(est["tokens_per_frame"], 1792, "⌈1568/28⌉×⌈882/28⌉ = 56×32")
        self.assertEqual(est["tokens"], 120 * 1792)
        self.assertAlmostEqual(est["coverage_sec"], 40.0)
        self.assertAlmostEqual(est["coverage_frac"], 40.0 / 600.0)

    def test_multi_zone_coverage_sums(self):
        est = DZ._estimate([(0.0, 10.0), (20.0, 30.0)], fps=3.0, width=512, height=288, duration=0.0)
        self.assertAlmostEqual(est["coverage_sec"], 20.0)
        self.assertEqual(est["frames"], 60)
        self.assertEqual(est["coverage_frac"], 0.0, "duration=0 → доля 0, без деления на ноль")


def _make_clip(path: Path, secs: int, size: str = "320x240") -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={secs}:size={size}:rate=10",
         "-pix_fmt", "yuv420p", str(path)],
        check=True)


def _write_journal(path: Path, records: list[dict]) -> None:
    path.write_text(json.dumps(records), encoding="utf-8")


@unittest.skipUnless(HAS_FFMPEG, "needs ffprobe/ffmpeg for get_metadata")
class TestBudgetGuard(unittest.TestCase):
    """Задача 1: на непрерывно меняющемся экране детектор склеивает всё видео в
    одну зону; второй проход при fps=3 перенарезал бы ~4500 кадров. Бюджет
    (FRAME_BUDGET=600) обязан отказать ДО нарезки — ни одного кадра на диск."""

    def _run_capture(self, **kw):
        """_run с захватом stdout; возвращает (exit_code, stdout_text)."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = DZ._run(**kw)
        return code, buf.getvalue()

    def _base_kwargs(self, journal, video, out_dir, **over):
        kw = dict(journal_path=str(journal), video=str(video), out_dir=str(out_dir),
                  fps=3.0, width=1568, threshold=10, pad=2.0, gap_merge=5.0,
                  min_zone=3.0, force=False)
        kw.update(over)
        return kw

    def test_live_coding_blocks_and_cuts_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"; _make_clip(clip, 3)
            journal = d / "j.json"
            _write_journal(journal, [{"timestamp": float(t), "category": "code"}
                                     for t in range(0, 1500, 2)])
            out = d / "zones"
            with mock.patch.object(F, "extract_fps1") as m_cut:
                code, out_text = self._run_capture(**self._base_kwargs(journal, clip, out))
            self.assertNotEqual(code, 0, "превышение бюджета → ненулевой exit")
            m_cut.assert_not_called()
            self.assertFalse(list(out.rglob("*.jpg")), "ни одного кадра на диске")
            self.assertIn("--force", out_text, "причина и подсказка идут в stdout")

    def test_force_overrides_budget_and_cuts(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"; _make_clip(clip, 3)
            journal = d / "j.json"
            _write_journal(journal, [{"timestamp": float(t), "category": "code"}
                                     for t in range(0, 1500, 2)])
            out = d / "zones"
            with mock.patch.object(F, "extract_fps1", return_value=[]) as m_cut:
                code, _ = self._run_capture(**self._base_kwargs(journal, clip, out, force=True))
            self.assertEqual(code, 0)
            m_cut.assert_called()

    def test_normal_demo_passes_and_cuts(self):
        """Демка внутри лекции — одна зона под бюджетом, режется молча (exit 0)."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"; _make_clip(clip, 3)
            journal = d / "j.json"
            recs = [{"timestamp": t / 2, "category": "demo"} for t in range(20, 61)]  # 10..30s
            _write_journal(journal, recs)
            out = d / "zones"
            with mock.patch.object(F, "extract_fps1", return_value=[]) as m_cut:
                code, out_text = self._run_capture(**self._base_kwargs(journal, clip, out))
            self.assertEqual(code, 0)
            m_cut.assert_called_once()
            self.assertIn("Зон найдено", out_text)

    def test_empty_journal_exits_zero_without_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"; _make_clip(clip, 1)
            journal = d / "j.json"; _write_journal(journal, [])
            out = d / "zones"
            with mock.patch.object(F, "extract_fps1") as m_cut:
                code, out_text = self._run_capture(**self._base_kwargs(journal, clip, out))
            self.assertEqual(code, 0)
            m_cut.assert_not_called()
            self.assertFalse(list(out.rglob("*.jpg")))


def _pattern(vertical: bool) -> "Image.Image":
    """Градиент. НЕ сплошная заливка: dHash сравнивает соседние пиксели, поэтому у
    любого однотонного кадра хеш нулевой — чёрный и белый экраны для него одинаковы."""
    img = Image.new("L", (64, 64))
    px = img.load()
    for x in range(64):
        for y in range(64):
            px[x, y] = min(255, (y if vertical else x) * 4)
    return img.convert("RGB").resize((320, 240))


def _flash_clip(path: Path, work: Path) -> None:
    """3 c при 10 fps: горизонтальный градиент, «вспышка» (вертикальный) на 1.5–1.9 c,
    снова горизонтальный. fps=1 сэмплит 0/1/2 c и вспышку не видит;
    fps=3 сэмплит ~1.67 c и видит."""
    work.mkdir(parents=True, exist_ok=True)
    for i in range(30):
        _pattern(vertical=15 <= i <= 19).save(work / f"src_{i:03d}.png")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-framerate", "10", "-i", str(work / "src_%03d.png"),
         "-pix_fmt", "yuv420p", str(path)],
        check=True)


def _static_clip(path: Path) -> None:
    """4 c однотонного серого при 10 fps — ни одной резкой смены сцены."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=gray:s=320x240:r=10", "-t", "4",
         "-pix_fmt", "yuv420p", str(path)],
        check=True)


@unittest.skipUnless(HAS_FFMPEG and HAS_PIL, "needs ffmpeg + Pillow")
class TestSceneChanges(unittest.TestCase):
    """Задача 2: событийный сэмплинг — кадр на СМЕНЕ сцены, а не на равномерной
    сетке fps, которая промахивается мимо состояния короче 1/fps секунды."""

    def test_timestamps_are_absolute(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"; _flash_clip(clip, d / "src")
            got = F.extract_scene_changes(str(clip), d / "sc", width=320,
                                          start=1.0, end=3.0, min_frames=1)
            self.assertTrue(got)
            self.assertTrue(all(g["timestamp"] >= 1.0 for g in got),
                            "окно (1,3): pts от нуля + start → таймкоды абсолютные")

    def test_scene_catches_the_flash(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"; _flash_clip(clip, d / "src")
            got = F.extract_scene_changes(str(clip), d / "sc", width=320,
                                          start=0.0, end=3.0, min_frames=2)
            reference = d / "flash.png"; _pattern(vertical=True).save(reference)
            fh = DD.dhash(str(reference))
            self.assertTrue(any(DD.hamming(DD.dhash(g["path"]), fh) < 10 for g in got),
                            "смена на входе вспышки обязана попасть в выдачу")

    def test_falls_back_when_too_few_scene_changes(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"; _static_clip(clip)
            got = F.extract_scene_changes(str(clip), d / "sc", width=320,
                                          start=0.0, end=4.0, min_frames=3)
            self.assertGreaterEqual(len(got), 3,
                                    "нет резких смен → фолбэк на равномерную нарезку")

    def test_scene_mode_runs_through_resample(self):
        """Сквозной путь resample_zone(mode='scene') — не падает, что-то возвращает."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"; _flash_clip(clip, d / "src")
            reps = DZ.resample_zone(str(clip), 0.0, 3.0, d / "z",
                                    width=320, fps=1.0, threshold=10, mode="scene")
            self.assertTrue(reps)


@unittest.skipUnless(HAS_FFMPEG and HAS_PIL, "needs ffmpeg + Pillow")
class TestResampleZone(unittest.TestCase):
    def test_fps_mode_matches_direct_extract(self):
        """Регресс: mode='fps' бит в бит совпадает с прямым extract_fps1 + dedup."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"; _flash_clip(clip, d / "src")
            via_mode = DZ.resample_zone(str(clip), 0.0, 3.0, d / "a",
                                        width=320, fps=3.0, threshold=10, mode="fps")
            raw = F.extract_fps1(str(clip), d / "b", width=320, fps=3.0, start=0.0, end=3.0)
            direct = DD.dedup_sequential(raw, threshold=10)
            self.assertEqual([Path(r["path"]).name for r in via_mode],
                             [Path(r["path"]).name for r in direct])
            self.assertEqual([r["timestamp"] for r in via_mode],
                             [r["timestamp"] for r in direct])

    def test_timestamps_are_absolute(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"
            _flash_clip(clip, d / "src")
            reps = DZ.resample_zone(str(clip), 1.0, 3.0, d / "zone",
                                    width=320, fps=1.0, threshold=10)
            self.assertTrue(reps)
            self.assertGreaterEqual(reps[0]["timestamp"], 1.0,
                                    "таймкоды окна — абсолютные, не от нуля")

    def test_dense_pass_recovers_what_fps1_flattened(self):
        """Смысл всей фичи: короткое состояние, которое fps=1 не сэмплит,
        второй проход обязан вернуть."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); clip = d / "clip.mp4"
            _flash_clip(clip, d / "src")
            sparse = DZ.resample_zone(str(clip), 0.0, 3.0, d / "sparse",
                                      width=320, fps=1.0, threshold=10)
            dense = DZ.resample_zone(str(clip), 0.0, 3.0, d / "dense",
                                     width=320, fps=3.0, threshold=10)
            self.assertEqual(len(sparse), 1, "fps=1 видит только фоновый узор")
            self.assertGreater(len(dense), len(sparse),
                               "fps=3 обязан поймать вспышку, которую fps=1 потерял")

            reference = d / "flash.png"
            _pattern(vertical=True).save(reference)
            flash_hash = DD.dhash(str(reference))

            def has_flash(reps) -> bool:
                return any(DD.hamming(DD.dhash(r["path"]), flash_hash) < 10 for r in reps)

            self.assertFalse(has_flash(sparse), "в редком проходе вспышки нет")
            self.assertTrue(has_flash(dense), "в плотном проходе вспышка есть")


if __name__ == "__main__":
    unittest.main()
