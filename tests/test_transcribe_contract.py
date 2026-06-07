from __future__ import annotations

import unittest

from src.schemas import Word
from src.transcribe import _project_free_timings_to_source


class TranscribeContractTest(unittest.TestCase):
    def test_free_timing_projection_handles_missing_short_tokens(self) -> None:
        source_text = (
            "Обязательно проверь, чтобы у тебя был конкретный список из достижений, "
            "это поможет тебе найти работу."
        )
        recognized = [
            Word(word="обязательно", start=0.08, end=0.56),
            Word(word="проверь", start=0.56, end=1.14),
            Word(word="чтобы", start=1.14, end=1.28),
            Word(word="его", start=1.28, end=1.52),
            Word(word="был", start=1.52, end=1.74),
            Word(word="конкретный", start=1.74, end=2.30),
            Word(word="список", start=2.30, end=2.74),
            Word(word="из", start=2.74, end=2.90),
            Word(word="достижений", start=2.90, end=3.42),
            Word(word="поможет", start=3.42, end=3.90),
            Word(word="тебе", start=3.90, end=4.08),
            Word(word="найти", start=4.08, end=4.32),
            Word(word="работу", start=4.32, end=4.56),
        ]

        corrected = _project_free_timings_to_source(recognized, source_text)

        self.assertIsNotNone(corrected)
        assert corrected is not None
        self.assertEqual([word.word for word in corrected], source_text.split())
        self.assertEqual(corrected[0].start, 0.08)
        self.assertEqual(corrected[-1].word, "работу.")
        self.assertEqual(corrected[-1].end, 4.56)


if __name__ == "__main__":
    unittest.main()
