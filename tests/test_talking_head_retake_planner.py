import json
import unittest
import sys
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import call, patch

from src.schemas import Transcript, Word
from pipelines import render_talking_head_dynamic_clean as talking_head_renderer
from pipelines.render_talking_head_dynamic_clean import EditChunk, SpeechSegment, plan_speech_with_retakes
from pipelines.talking_head_retake_planner import TimedRange, plan_retakes


def _transcript(phrases: list[list[str]], *, gap: float = 0.8) -> Transcript:
    words: list[Word] = []
    cursor = 0.0
    for phrase in phrases:
        for token in phrase:
            words.append(Word(word=token, start=cursor, end=cursor + 0.22))
            cursor += 0.28
        cursor += gap
    return Transcript(
        words=words,
        full_text=" ".join(word.word for word in words),
        duration=words[-1].end if words else 0.0,
    )


def _transcript_from_words(
    words_with_times: list[tuple[str, float, float]],
    *,
    duration: float | None = None,
) -> Transcript:
    words = [Word(word=word, start=start, end=end) for word, start, end in words_with_times]
    return Transcript(
        words=words,
        full_text=" ".join(word.word for word in words),
        duration=duration if duration is not None else (words[-1].end if words else 0.0),
    )


class TalkingHeadRetakePlannerTests(unittest.TestCase):
    def test_removes_short_early_duplicate_and_keeps_later_attempt(self) -> None:
        transcript = _transcript(
            [
                ["есть", "стримы"],
                ["есть", "понятные", "стримы", "как", "я", "буду", "зарабатывать"],
            ]
        )

        plan = plan_retakes(
            transcript,
            [TimedRange(source_index=0, start=0.0, end=transcript.duration)],
            mode="safe",
            confidence_threshold=0.78,
        )

        self.assertEqual(len(plan.decisions), 1)
        self.assertEqual(plan.decisions[0].status, "applied")
        self.assertGreaterEqual(plan.speech_segments[0].start, 1.0)

    def test_prefers_clean_attempt_over_cough_attempt(self) -> None:
        transcript = _transcript(
            [
                ["есть", "понятные", "стримы", "кхм"],
                ["есть", "понятные", "стримы", "как", "я", "буду", "зарабатывать"],
            ]
        )

        plan = plan_retakes(
            transcript,
            [TimedRange(source_index=0, start=0.0, end=transcript.duration)],
            mode="safe",
            confidence_threshold=0.78,
        )

        self.assertEqual(plan.decisions[0].status, "applied")
        chosen = plan.decisions[0].chosen_ranges[0]
        self.assertGreater(chosen.start, 1.0)
        self.assertNotIn("кхм", plan.decisions[0].attempts[-1].tokens)

    def test_smart_splice_replaces_failed_suffix_restart(self) -> None:
        transcript = _transcript(
            [
                [
                    "есть",
                    "понятные",
                    "стримы",
                    "как",
                    "я",
                    "буду",
                    "зарабатывать",
                    "но",
                    "уверенности",
                    "до",
                    "конца",
                ],
                ["но", "уверенности", "что", "получится", "до", "конца", "нет"],
            ]
        )

        plan = plan_retakes(
            transcript,
            [TimedRange(source_index=0, start=0.0, end=transcript.duration)],
            mode="smart",
            confidence_threshold=0.78,
        )

        self.assertEqual(plan.decisions[0].status, "applied")
        self.assertEqual(plan.decisions[0].reason, "smart_splice_restart")
        self.assertEqual(len(plan.decisions[0].chosen_ranges), 2)
        self.assertEqual(plan.decisions[0].splice["overlap_tokens"], ["но", "уверенности"])

    def test_suffix_repair_splice_removes_incomplete_tail_before_clean_suffix(self) -> None:
        transcript = _transcript(
            [
                ["есть", "стримы"],
                ["есть", "понятные", "стримы", "как", "я", "буду", "зарабатывать"],
                [
                    "есть",
                    "понятные",
                    "стримы",
                    "как",
                    "я",
                    "буду",
                    "зарабатывать",
                    "но",
                    "уверенности",
                    "до",
                    "конца",
                ],
                ["уверенности", "что", "получится", "до", "конца", "нет"],
            ]
        )

        plan = plan_retakes(
            transcript,
            [TimedRange(source_index=0, start=0.0, end=transcript.duration)],
            mode="smart",
            confidence_threshold=0.78,
        )

        decision = plan.decisions[0]
        self.assertEqual(decision.status, "applied")
        self.assertEqual(decision.reason, "suffix_repair_splice")
        self.assertEqual(decision.group_type, "suffix_repair_splice")
        self.assertEqual(len(decision.chosen_ranges), 2)
        self.assertLess(decision.chosen_ranges[0].end, transcript.words[18].start + 0.001)
        self.assertGreaterEqual(decision.chosen_ranges[1].start, transcript.words[20].start - 0.121)

    def test_prefix_restart_repair_removes_orphan_prefix_before_full_phrase(self) -> None:
        transcript = _transcript_from_words(
            [
                ("это", 0.38, 0.62),
                ("вид", 4.66, 4.78),
                ("москвы", 4.78, 5.22),
                ("потому", 6.68, 6.80),
                ("что", 6.80, 7.20),
                ("уже", 7.20, 8.20),
                ("потому", 13.42, 14.42),
                ("что", 15.12, 15.42),
                ("уезжаю", 15.42, 15.76),
                ("саратов", 15.76, 16.60),
                ("я", 16.60, 18.10),
                ("уволился", 18.18, 18.50),
                ("с", 18.50, 18.64),
                ("работы", 18.64, 18.88),
            ]
        )

        plan = plan_retakes(
            transcript,
            [
                TimedRange(source_index=0, start=0.0, end=5.59),
                TimedRange(source_index=0, start=6.56, end=8.32),
                TimedRange(source_index=0, start=14.87, end=16.93),
                TimedRange(source_index=0, start=17.90, end=19.39),
            ],
            mode="smart",
            confidence_threshold=0.78,
        )

        decision = plan.decisions[0]
        self.assertEqual(decision.status, "applied")
        self.assertEqual(decision.reason, "prefix_restart_repair")
        self.assertEqual(decision.group_type, "prefix_restart_repair")
        removed_texts = [
            attempt.text for attempt in decision.attempts if attempt.index in decision.removed_attempt_indexes
        ]
        self.assertEqual(removed_texts, ["потому что уже"])
        self.assertEqual(len(decision.kept_attempt_indexes), 2)
        self.assertTrue(all(not (6.0 <= segment.start <= 9.0) for segment in plan.speech_segments))
        self.assertTrue(any(segment.start == 14.87 for segment in plan.speech_segments))

    def test_does_not_group_only_superficially_similar_phrases(self) -> None:
        transcript = _transcript(
            [
                ["есть", "понятные", "стримы"],
                ["есть", "риски", "в", "проекте"],
            ]
        )

        plan = plan_retakes(
            transcript,
            [TimedRange(source_index=0, start=0.0, end=transcript.duration)],
            mode="smart",
            confidence_threshold=0.78,
        )

        self.assertEqual(plan.decisions, [])
        self.assertEqual(plan.speech_segments, [TimedRange(source_index=0, start=0.0, end=round(transcript.duration, 3))])

    def test_does_not_group_clean_contrast_phrases(self) -> None:
        transcript = _transcript(
            [
                [
                    "с",
                    "одной",
                    "стороны",
                    "вы",
                    "можете",
                    "работать",
                    "удаленно",
                    "и",
                    "зарабатывать",
                ],
                [
                    "с",
                    "другой",
                    "стороны",
                    "вы",
                    "можете",
                    "уволиться",
                    "и",
                    "начать",
                    "работать",
                    "на",
                    "себя",
                ],
            ]
        )

        plan = plan_retakes(
            transcript,
            [TimedRange(source_index=0, start=0.0, end=transcript.duration)],
            mode="smart",
            confidence_threshold=0.78,
        )

        self.assertEqual(plan.decisions, [])

    def test_restart_repair_removes_late_failed_take_and_keeps_clean_rephrase(self) -> None:
        transcript = _transcript(
            [
                ["это", "некий", "эксперимент"],
                [
                    "очень",
                    "дорогое",
                    "для",
                    "меня",
                    "и",
                    "я",
                    "воспресоединяю",
                    "и",
                    "у",
                    "вас",
                    "это",
                    "некий",
                    "эксперимент",
                    "довольно",
                    "дорогое",
                    "под",
                    "то",
                    "же",
                    "цена",
                    "ошибки",
                    "серьезно",
                ],
                ["для", "меня", "это", "эксперимент", "в", "первую", "очередь"],
                ["я", "не", "знаю", "что", "получится"],
            ]
        )

        plan = plan_retakes(
            transcript,
            [TimedRange(source_index=0, start=0.0, end=transcript.duration)],
            mode="smart",
            confidence_threshold=0.78,
        )

        repair = next(decision for decision in plan.decisions if decision.group_type == "restart_repair")
        self.assertEqual(repair.status, "applied")
        self.assertEqual(repair.reason, "restart_repair")
        self.assertEqual(repair.removed_attempt_indexes, [0, 1])
        self.assertEqual(repair.kept_attempt_indexes, [2, 3])
        self.assertGreaterEqual(plan.speech_segments[0].start, transcript.words[24].start - 0.121)

    def test_local_phrase_restart_removes_only_failed_prefix(self) -> None:
        transcript = _transcript_from_words(
            [
                ("а", 0.00, 0.10),
                ("иногда", 0.12, 0.34),
                ("даже", 0.36, 0.54),
                ("хороший", 0.56, 0.82),
                ("результа", 0.84, 1.02),
                ("а", 1.34, 1.44),
                ("иногда", 1.46, 1.68),
                ("даже", 1.70, 1.88),
                ("тот", 1.90, 2.08),
                ("результат", 2.10, 2.36),
                ("который", 2.38, 2.62),
                ("ты", 2.64, 2.76),
                ("сделал", 2.78, 3.04),
                ("уже", 3.06, 3.20),
                ("не", 3.22, 3.34),
                ("радует", 3.36, 3.62),
                ("дальше", 3.64, 3.90),
                ("новая", 3.92, 4.12),
                ("мысль", 4.14, 4.36),
            ]
        )

        plan = plan_retakes(
            transcript,
            [TimedRange(source_index=0, start=0.0, end=transcript.duration)],
            mode="smart",
            confidence_threshold=0.78,
        )

        decision = plan.decisions[0]
        self.assertEqual(decision.status, "applied")
        self.assertEqual(decision.reason, "local_phrase_restart")
        self.assertEqual(decision.group_type, "local_phrase_restart")
        self.assertLessEqual(decision.removed_ranges[0].duration, 1.5)
        self.assertGreaterEqual(plan.speech_segments[0].start, 1.22)
        self.assertEqual(plan.speech_segments[-1].end, round(transcript.duration, 3))

    def test_local_phrase_restart_ignores_clean_repeated_enumeration(self) -> None:
        transcript = _transcript_from_words(
            [
                ("результатов", 0.00, 0.28),
                ("пока", 0.30, 0.46),
                ("нет", 0.48, 0.62),
                ("ни", 0.64, 0.74),
                ("в", 0.76, 0.84),
                ("деньгах", 0.86, 1.12),
                ("ни", 1.34, 1.44),
                ("в", 1.46, 1.54),
                ("каких", 1.56, 1.76),
                ("то", 1.78, 1.88),
                ("завершенных", 1.90, 2.22),
                ("продуктах", 2.24, 2.54),
            ]
        )

        plan = plan_retakes(
            transcript,
            [TimedRange(source_index=0, start=0.0, end=transcript.duration)],
            mode="smart",
            confidence_threshold=0.78,
        )

        self.assertEqual(plan.decisions, [])
        self.assertEqual(plan.speech_segments, [TimedRange(source_index=0, start=0.0, end=round(transcript.duration, 3))])

    def test_correction_marker_rephrase_removes_abandoned_phrase(self) -> None:
        transcript = _transcript_from_words(
            [
                ("я", 13.86, 13.96),
                ("получилась", 13.96, 14.38),
                ("вот", 14.38, 14.56),
                ("такая", 14.56, 14.82),
                ("карусель", 14.82, 15.32),
                ("точнее", 16.02, 16.36),
                ("у", 17.58, 17.68),
                ("меня", 17.68, 17.72),
                ("получился", 17.72, 18.32),
                ("вот", 18.32, 18.92),
                ("такой", 18.92, 19.18),
                ("слайд", 19.44, 19.74),
                ("из", 19.74, 19.84),
                ("четырех", 20.00, 20.42),
                ("похожих", 20.42, 20.82),
            ],
            duration=21.0,
        )

        plan = plan_retakes(
            transcript,
            [
                TimedRange(source_index=0, start=13.61, end=15.61),
                TimedRange(source_index=0, start=15.89, end=16.75),
                TimedRange(source_index=0, start=17.33, end=21.0),
            ],
            mode="smart",
            confidence_threshold=0.78,
        )

        decision = plan.decisions[0]
        self.assertEqual(decision.status, "applied")
        self.assertEqual(decision.reason, "correction_marker_rephrase")
        self.assertEqual(decision.group_type, "correction_marker_rephrase")
        self.assertTrue(all(segment.start >= 17.4 for segment in plan.speech_segments), plan.speech_segments)

    def test_large_restart_repair_with_low_semantic_coverage_requires_review(self) -> None:
        transcript = _transcript(
            [
                [
                    "вот",
                    "так",
                    "случилось",
                    "у",
                    "меня",
                    "я",
                    "сделал",
                    "приложение",
                    "которое",
                    "помогает",
                    "мне",
                    "развивать",
                    "директ",
                ],
                ["но", "прошло", "две", "недели", "от", "момента", "появления", "идеи"],
                [
                    "и",
                    "кажется",
                    "что",
                    "это",
                    "так",
                    "долго",
                    "что",
                    "сейчас",
                    "я",
                    "смотрю",
                    "и",
                    "думаю",
                    "ну",
                    "это",
                    "очевидно",
                    "это",
                    "же",
                    "очевидно",
                ],
                ["ну", "что", "очевидно", "зачем", "я", "общаюсь", "с", "мысленным", "голосом"],
            ]
        )

        plan = plan_retakes(
            transcript,
            [TimedRange(source_index=0, start=0.0, end=transcript.duration)],
            mode="smart",
            confidence_threshold=0.78,
        )

        repair = next(decision for decision in plan.decisions if decision.group_type == "restart_repair")
        self.assertEqual(repair.status, "needs_review")
        self.assertGreater(repair.removed_ranges[0].duration, 8.0)
        self.assertLess(repair.features["anchor_coverage"], 0.25)
        self.assertEqual(plan.speech_segments, [TimedRange(source_index=0, start=0.0, end=round(transcript.duration, 3))])

    def test_pruned_retake_does_not_leave_micro_fragments(self) -> None:
        transcript = _transcript_from_words(
            [
                ("уже", 0.40, 0.52),
                ("смешно", 0.52, 0.84),
                ("уже", 2.00, 2.12),
                ("смешно", 2.12, 2.44),
            ]
        )

        plan = plan_retakes(
            transcript,
            [
                TimedRange(source_index=0, start=0.0, end=0.6),
                TimedRange(source_index=0, start=0.7, end=2.7),
            ],
            mode="smart",
            confidence_threshold=0.78,
        )

        self.assertTrue(all(segment.duration >= 0.35 for segment in plan.speech_segments))
        self.assertTrue(all(segment.start >= 1.88 for segment in plan.speech_segments))

    def test_chosen_ranges_preserve_base_silence_splits(self) -> None:
        transcript = _transcript(
            [
                ["есть", "стримы"],
                ["есть", "понятные", "стримы", "как", "я", "буду", "зарабатывать"],
            ]
        )

        plan = plan_retakes(
            transcript,
            [
                TimedRange(source_index=0, start=0.0, end=0.6),
                TimedRange(source_index=0, start=1.24, end=2.0),
                TimedRange(source_index=0, start=2.6, end=transcript.duration),
            ],
            mode="smart",
            confidence_threshold=0.78,
        )

        self.assertEqual(len(plan.speech_segments), 2)
        self.assertLess(plan.speech_segments[0].end, plan.speech_segments[1].start)

    def test_renderer_retake_off_does_not_transcribe(self) -> None:
        args = Namespace(
            retake_mode="off",
            retake_confidence_threshold=0.78,
            transcribe_model="base",
        )
        with patch("pipelines.render_talking_head_dynamic_clean.transcribe_source") as transcribe_source:
            speech, reviews = plan_speech_with_retakes(
                sources=[Path("source.mov")],
                speech=[SpeechSegment(source_index=0, start=0.0, end=2.0)],
                out_dir=Path("output/test"),
                args=args,
            )

        transcribe_source.assert_not_called()
        self.assertEqual(speech, [SpeechSegment(source_index=0, start=0.0, end=2.0)])
        self.assertEqual(reviews[0]["mode"], "off")

    def test_renderer_smart_mode_returns_review_decision(self) -> None:
        transcript = _transcript(
            [
                ["есть", "стримы"],
                ["есть", "понятные", "стримы", "как", "я", "буду", "зарабатывать"],
            ]
        )
        args = Namespace(
            retake_mode="smart",
            retake_confidence_threshold=0.78,
            transcribe_model="base",
        )

        with patch("pipelines.render_talking_head_dynamic_clean.transcribe_source", return_value=transcript):
            speech, reviews = plan_speech_with_retakes(
                sources=[Path("source.mov")],
                speech=[SpeechSegment(source_index=0, start=0.0, end=transcript.duration)],
                out_dir=Path("output/test"),
                args=args,
            )

        self.assertGreaterEqual(speech[0].start, 1.0)
        self.assertEqual(reviews[0]["summary"]["applied"], 1)
        self.assertEqual(reviews[0]["decisions"][0]["status"], "applied")

    def test_renderer_uses_auxiliary_base_transcript_for_hidden_short_duplicate(self) -> None:
        primary = _transcript_from_words(
            [
                ("представьте", 0.0, 0.2),
                ("выбор", 0.4, 0.6),
                ("уже", 1.4, 1.5),
                ("смешно", 1.5, 1.8),
                ("с", 1.9, 2.6),
            ]
        )
        auxiliary = _transcript_from_words(
            [
                ("уже", 1.4, 1.5),
                ("смешно", 1.5, 1.8),
                ("уже", 3.0, 3.1),
                ("смешно", 3.1, 3.4),
            ]
        )
        args = Namespace(
            retake_mode="smart",
            retake_confidence_threshold=0.78,
            transcribe_model="large-v3",
        )

        with patch("pipelines.render_talking_head_dynamic_clean.transcribe_source", side_effect=[primary, auxiliary]) as transcribe_source:
            speech, reviews = plan_speech_with_retakes(
                sources=[Path("source.mov")],
                speech=[
                    SpeechSegment(source_index=0, start=0.0, end=2.2),
                    SpeechSegment(source_index=0, start=2.8, end=3.8),
                ],
                out_dir=Path("output/test"),
                args=args,
            )

        transcribe_source.assert_has_calls(
            [
                call(Path("source.mov"), Path("output/test"), 0, "large-v3"),
                call(Path("source.mov"), Path("output/test"), 0, "base"),
            ]
        )
        self.assertEqual(reviews[0]["auxiliary_transcript_paths"], ["output/test/source_00.source.base.transcript.json"])
        self.assertEqual(reviews[0]["decisions"][0]["transcript_source"], "auxiliary-base")
        self.assertEqual(speech[0], SpeechSegment(source_index=0, start=0.0, end=1.28))
        self.assertEqual(speech[1], SpeechSegment(source_index=0, start=2.88, end=3.8))

    def test_renderer_removes_short_restart_across_source_boundary(self) -> None:
        first = _transcript(
            [
                ["ты", "думаешь", "что", "маленькие", "результаты", "уже", "не", "радуют"],
                ["а", "иногда", "даже", "хороший", "результа"],
            ],
            gap=0.9,
        )
        second = _transcript(
            [
                ["а", "иногда", "даже", "тот", "результат", "который", "ты", "сделал", "уже", "не", "радует"],
            ]
        )
        args = Namespace(
            retake_mode="smart",
            retake_confidence_threshold=0.78,
            transcribe_model="base",
        )

        with patch("pipelines.render_talking_head_dynamic_clean.transcribe_source", side_effect=[first, second]):
            speech, reviews = plan_speech_with_retakes(
                sources=[Path("first.mov"), Path("second.mov")],
                speech=[
                    SpeechSegment(source_index=0, start=0.0, end=first.duration),
                    SpeechSegment(source_index=1, start=0.0, end=second.duration),
                ],
                out_dir=Path("output/test"),
                args=args,
            )

        boundary_review = reviews[-1]
        self.assertEqual(boundary_review["source"], "source_boundary")
        self.assertEqual(boundary_review["summary"]["applied"], 1)
        self.assertEqual(boundary_review["decisions"][0]["reason"], "boundary_phrase_restart")
        self.assertTrue(all(not (segment.source_index == 0 and segment.end > first.words[8].start) for segment in speech))
        self.assertTrue(any(segment.source_index == 1 and segment.start == 0.0 for segment in speech))

    def test_renderer_boundary_restart_removes_wordless_orphan_tail(self) -> None:
        first = _transcript_from_words(
            [
                ("ты", 0.00, 0.16),
                ("думаешь", 0.18, 0.44),
                ("что", 0.46, 0.58),
                ("путь", 0.60, 0.82),
                ("уже", 0.84, 0.98),
                ("не", 1.00, 1.10),
                ("радует", 1.12, 1.36),
                ("а", 2.00, 2.10),
                ("иногда", 2.12, 2.34),
                ("даже", 2.36, 2.54),
                ("хороший", 2.56, 2.82),
                ("результа", 2.84, 3.10),
            ],
            duration=4.05,
        )
        second = _transcript_from_words(
            [
                ("а", 0.72, 0.82),
                ("иногда", 0.84, 1.06),
                ("даже", 1.08, 1.26),
                ("тот", 1.28, 1.44),
                ("результат", 1.46, 1.74),
                ("который", 1.76, 2.02),
                ("ты", 2.04, 2.16),
                ("сделал", 2.18, 2.44),
                ("уже", 2.46, 2.60),
                ("не", 2.62, 2.72),
                ("радует", 2.74, 3.00),
            ],
            duration=3.2,
        )
        args = Namespace(
            retake_mode="smart",
            retake_confidence_threshold=0.78,
            transcribe_model="base",
        )

        with patch("pipelines.render_talking_head_dynamic_clean.transcribe_source", side_effect=[first, second]):
            speech, reviews = plan_speech_with_retakes(
                sources=[Path("first.mov"), Path("second.mov")],
                speech=[
                    SpeechSegment(source_index=0, start=0.0, end=4.05),
                    SpeechSegment(source_index=1, start=0.0, end=3.2),
                ],
                out_dir=Path("output/test"),
                args=args,
            )

        boundary_review = reviews[-1]
        self.assertEqual(boundary_review["decisions"][0]["reason"], "boundary_phrase_restart")
        self.assertTrue(
            all(not (segment.source_index == 0 and segment.start >= 3.0) for segment in speech),
            speech,
        )
        self.assertEqual(boundary_review["decisions"][0]["features"]["orphan_tail_action"], "extended_removed_range")

    def test_renderer_boundary_prefix_rephrase_removes_abandoned_take(self) -> None:
        first = _transcript_from_words(
            [
                ("поэтому", 0.44, 0.74),
                ("они", 0.76, 0.96),
                ("потратили", 0.98, 1.32),
                ("много", 8.52, 8.80),
                ("времени", 8.80, 9.14),
                ("чтобы", 9.46, 9.68),
                ("это", 9.68, 9.88),
                ("работало", 10.38, 10.84),
                ("у", 11.62, 11.72),
                ("себя", 11.72, 11.88),
                ("в", 11.88, 12.02),
                ("тг", 12.08, 12.28),
                ("я", 12.28, 12.52),
                ("показываю", 12.52, 13.22),
                ("результат", 13.22, 13.72),
                ("с", 15.24, 15.34),
                ("летай", 15.34, 15.78),
                ("смотри", 16.00, 16.40),
                ("коменте", 16.70, 17.16),
                ("возможно", 18.10, 18.36),
                ("скоро", 18.36, 18.64),
                ("я", 18.64, 18.76),
                ("пошарю", 18.76, 19.12),
                ("этот", 19.12, 19.30),
                ("скид", 19.30, 19.56),
            ],
            duration=19.56,
        )
        second = _transcript_from_words(
            [
                ("у", 0.98, 1.08),
                ("себя", 1.08, 1.28),
                ("в", 1.28, 1.40),
                ("тг", 1.40, 1.58),
                ("я", 1.58, 1.72),
                ("скоро", 1.72, 2.00),
                ("пошарю", 2.00, 2.36),
                ("этот", 2.36, 2.54),
                ("апчик", 2.54, 2.90),
                ("тоже", 2.90, 3.20),
                ("можно", 3.20, 3.52),
                ("пользоваться", 3.52, 4.10),
            ],
            duration=4.2,
        )
        args = Namespace(
            retake_mode="smart",
            retake_confidence_threshold=0.78,
            transcribe_model="base",
        )

        with patch("pipelines.render_talking_head_dynamic_clean.transcribe_source", side_effect=[first, second]):
            speech, reviews = plan_speech_with_retakes(
                sources=[Path("first.mov"), Path("second.mov")],
                speech=[
                    SpeechSegment(source_index=0, start=0.44, end=11.11),
                    SpeechSegment(source_index=0, start=11.42, end=14.41),
                    SpeechSegment(source_index=0, start=15.02, end=17.56),
                    SpeechSegment(source_index=0, start=17.81, end=19.69),
                    SpeechSegment(source_index=1, start=0.77, end=4.2),
                ],
                out_dir=Path("output/test"),
                args=args,
            )

        boundary_review = reviews[-1]
        self.assertEqual(boundary_review["decisions"][0]["reason"], "boundary_prefix_rephrase_restart")
        self.assertTrue(all(not (segment.source_index == 0 and segment.start >= 11.0) for segment in speech), speech)
        self.assertTrue(any(segment.source_index == 1 and segment.start == 0.77 for segment in speech))

    def test_renderer_boundary_restart_ignores_generic_overlap(self) -> None:
        first = _transcript(
            [
                ["ну", "что", "очевидно", "зачем", "я", "общаюсь", "с", "мысленным", "голосом"],
            ]
        )
        second = _transcript(
            [
                [
                    "и",
                    "тут",
                    "нет",
                    "никакой",
                    "умной",
                    "мысли",
                    "потому",
                    "что",
                    "я",
                    "не",
                    "знаю",
                    "как",
                    "с",
                    "ним",
                    "бороться",
                ],
            ]
        )
        args = Namespace(
            retake_mode="smart",
            retake_confidence_threshold=0.78,
            transcribe_model="base",
        )

        with patch("pipelines.render_talking_head_dynamic_clean.transcribe_source", side_effect=[first, second]):
            speech, reviews = plan_speech_with_retakes(
                sources=[Path("first.mov"), Path("second.mov")],
                speech=[
                    SpeechSegment(source_index=0, start=0.0, end=first.duration),
                    SpeechSegment(source_index=1, start=0.0, end=second.duration),
                ],
                out_dir=Path("output/test"),
                args=args,
            )

        self.assertEqual(len(reviews), 2)
        self.assertEqual(
            speech,
            [
                SpeechSegment(source_index=0, start=0.0, end=round(first.duration, 3)),
                SpeechSegment(source_index=1, start=0.0, end=round(second.duration, 3)),
            ],
        )

    def test_chunks_with_output_timing_accounts_for_crossfades(self) -> None:
        from pipelines.render_talking_head_dynamic_clean import EditChunk, chunks_with_output_timing

        timed = chunks_with_output_timing(
            [
                EditChunk(0, 0, "a.mov", 0.0, 4.42, 4.42, "medium", "micro_push"),
                EditChunk(1, 0, "a.mov", 5.0, 5.91, 0.91, "medium", "micro_push"),
                EditChunk(2, 1, "b.mov", 0.0, 6.58, 6.58, "close", "micro_push"),
            ],
            transition_duration=0.16,
        )

        self.assertEqual(timed[0]["out_start"], 0.0)
        self.assertEqual(timed[0]["out_end"], 4.42)
        self.assertEqual(timed[1]["out_start"], 4.26)
        self.assertEqual(timed[1]["out_end"], 5.17)
        self.assertEqual(timed[2]["out_start"], 5.01)
        self.assertEqual(timed[2]["out_end"], 11.59)

    def test_talking_head_subtitle_transcript_maps_words_to_output_timeline(self) -> None:
        first = _transcript_from_words(
            [
                ("первое", 10.10, 10.40),
                ("слово", 10.50, 10.90),
            ],
            duration=12.0,
        )
        second = _transcript_from_words(
            [
                ("дальше", 0.20, 0.55),
            ],
            duration=2.0,
        )

        transcript = talking_head_renderer.build_talking_head_timeline_transcript(
            [
                EditChunk(0, 0, "a.mov", 10.0, 11.0, 1.0, "medium", "micro_push"),
                EditChunk(1, 1, "b.mov", 0.0, 2.0, 2.0, "close", "micro_push"),
            ],
            {0: first, 1: second},
            transition_duration=0.2,
        )

        self.assertEqual(transcript.duration, 2.8)
        self.assertEqual(transcript.full_text, "первое слово дальше")
        self.assertEqual(
            [(word.word, round(word.start, 2), round(word.end, 2)) for word in transcript.words],
            [
                ("первое", 0.10, 0.40),
                ("слово", 0.50, 0.90),
                ("дальше", 1.00, 1.35),
            ],
        )

    def test_talking_head_cli_accepts_optional_subtitles(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "render_talking_head_dynamic_clean.py",
                "--slug",
                "sample",
                "--input",
                "input.mov",
                "--subtitles",
                "--subtitle-style",
                "editorial_pop",
            ],
        ):
            args = talking_head_renderer.parse_args()

        self.assertTrue(args.subtitles)
        self.assertEqual(args.subtitle_style, "editorial_pop")

    def test_explicit_edit_plan_can_rotate_a_screen_chunk(self) -> None:
        from pipelines.render_talking_head_dynamic_clean import chunk_video_filter, load_decision_chunks

        with TemporaryDirectory() as tmp:
            plan = Path(tmp) / "edit_plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "chunks": [
                            {
                                "index": 0,
                                "source_index": 0,
                                "source": "screen.mov",
                                "start": 1.0,
                                "end": 4.0,
                                "duration": 3.0,
                                "plan": "medium",
                                "transition": "micro_push",
                                "video_transform": "rotate180",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            chunks = load_decision_chunks(plan)

        self.assertEqual(chunks[0].video_transform, "rotate180")
        self.assertIn("hflip,vflip", chunk_video_filter(chunks[0], None, "[vout]"))

    def test_quality_report_flags_short_wordless_boundary_chunk(self) -> None:
        from pipelines.render_talking_head_dynamic_clean import EditChunk, build_timeline_quality_report

        transcript = _transcript_from_words(
            [
                ("готовый", 0.0, 0.2),
                ("текст", 0.22, 0.44),
            ],
            duration=4.2,
        )
        report = build_timeline_quality_report(
            chunks=[
                EditChunk(0, 0, "a.mov", 0.0, 1.8, 1.8, "medium", "micro_push"),
                EditChunk(1, 0, "a.mov", 3.2, 4.05, 0.85, "medium", "micro_push"),
                EditChunk(2, 1, "b.mov", 0.0, 2.4, 2.4, "close", "micro_push"),
            ],
            transcripts_by_source={0: transcript},
            transition_duration=0.16,
        )

        self.assertEqual(report["status"], "fail")
        self.assertIn("source_transitions", report)
        self.assertTrue(
            any(issue["code"] == "short_wordless_boundary_chunk" for issue in report["issues"]),
            report,
        )

    def test_organizes_root_talking_head_sources_with_sidecars(self) -> None:
        from pipelines.render_talking_head_dynamic_clean import organize_talking_head_sources

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "IMG_0001.MOV"
            transcript = root / "IMG_0001.transcript.json"
            transcript_hash = root / "IMG_0001.transcript.json.hash"
            source.write_bytes(b"video")
            transcript.write_text("{}", encoding="utf-8")
            transcript_hash.write_text("hash", encoding="utf-8")

            organized = organize_talking_head_sources([source], slug="episode-1", root=root)

            archive_dir = root / "assets" / "talking_head_sources" / "episode-1"
            archived_source = archive_dir / "IMG_0001.MOV"
            self.assertEqual(organized, [archived_source])
            self.assertFalse(source.exists())
            self.assertTrue(archived_source.exists())
            self.assertTrue((archive_dir / "IMG_0001.transcript.json").exists())
            self.assertTrue((archive_dir / "IMG_0001.transcript.json.hash").exists())

            organized_again = organize_talking_head_sources([source], slug="episode-1", root=root)

            self.assertEqual(organized_again, [archived_source])


if __name__ == "__main__":
    unittest.main()
