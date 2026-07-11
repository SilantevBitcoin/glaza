import unittest
from pathlib import Path
import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import glaza


class TestRenderReport(unittest.TestCase):
    def _report(self, **kw):
        reps = [{"index": 4, "timestamp": 50.0, "path": "/f/frame_00051.jpg",
                 "span_end_timestamp": 54.67}]
        segs = [{"start": 1.0, "end": 2.0, "text": "открой терминал"}]
        base = dict(source="v.mp4", title="Demo", duration=120.0, reps=reps,
                    segments=segs, work=Path("/tmp/w"))
        base.update(kw)
        return glaza.render_report(**base)

    def test_sections_and_paths(self):
        out = self._report()
        self.assertIn("Уникальные кадры", out)
        self.assertIn("frame_00051.jpg", out)
        self.assertIn("## Transcript", out)

    def test_machine_readable_seconds_for_extract_one(self):
        """Step 6 передаёт span_end_timestamp в extract_one — секунды обязаны
        быть в отчёте, иначе модель пересчитывает MM:SS вручную и теряет доли."""
        out = self._report()
        self.assertIn("[50.00s]", out)
        self.assertIn("span_end=00:55 [54.67s]", out)

    def test_does_not_tell_the_main_model_to_read_frames(self):
        out = self._report()
        self.assertIn("Сам эти кадры не открывай", out)

    def test_intent_appears_in_header(self):
        self.assertIn("зачем смотрю", self._report(intent="зачем смотрю"))

    def test_focus_report_is_utf8_encodable(self):
        out = self._report(title="Тест 视频", reps=[], segments=[], focus=(1.0, 5.0))
        self.assertIn("→", out)
        out.encode("utf-8")  # must not raise


class TestTranscriptSection(unittest.TestCase):
    def test_backend_failure_is_loud_and_not_confused_with_silence(self):
        out = glaza.render_transcript_section([], "faster-whisper not installed", "")
        self.assertIn("ОШИБКА", out)
        self.assertIn("faster-whisper not installed", out)
        self.assertNotIn("нет аудиодорожки", out)

    def test_no_audio_is_a_note_not_an_error(self):
        out = glaza.render_transcript_section([], None, "_в видео нет аудиодорожки_")
        self.assertNotIn("ОШИБКА", out)
        self.assertIn("нет аудиодорожки", out)


class TestCfgHelpers(unittest.TestCase):
    def test_cfg_int_empty_string_falls_back(self):
        self.assertEqual(glaza._cfg_int({"K": ""}, "K", 80), 80)

    def test_cfg_int_valid_value(self):
        self.assertEqual(glaza._cfg_int({"K": "5"}, "K", 80), 5)

    def test_cfg_int_missing_key(self):
        self.assertEqual(glaza._cfg_int({}, "K", 80), 80)

    def test_pick_treats_zero_as_a_value(self):
        """--dedup-threshold 0 = выключить дедуп, а не «взять дефолт»."""
        self.assertEqual(glaza._pick(0, 10), 0)
        self.assertEqual(glaza._pick(None, 10), 10)
        self.assertEqual(glaza._pick(3, 10), 3)


if __name__ == "__main__":
    unittest.main()
