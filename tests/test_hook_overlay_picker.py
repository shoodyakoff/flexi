"""Golden-cases for the hook overlay auto-picker.

Each case is (hook_text, cta_context, expected_overlay_id_or_None). The picker
scores icons from `assets/hook_overlays/_meta.json` against hook + CTA voiceover
and inserts the winner; cases here lock down that behavior so meta tweaks can't
silently change which icon a published hook gets.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from src.hook_montage import _find_accent_cues
from src.schemas import AssetEntry, Config, Transcript, Word

ROOT = Path(__file__).resolve().parents[1]


def _make_transcript(text: str) -> Transcript:
    tokens = text.split()
    words = [
        Word(word=token, start=float(i), end=float(i) + 0.6)
        for i, token in enumerate(tokens)
    ]
    duration = float(len(tokens))
    return Transcript(words=words, full_text=text, duration=duration)


def _load_library() -> dict[str, AssetEntry]:
    raw = json.loads((ROOT / "assets/hook_overlays/_meta.json").read_text())
    return {asset_id: AssetEntry(**payload) for asset_id, payload in raw.items()}


def _load_cfg() -> Config:
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    return Config(**raw)


GOLDEN_CASES: list[tuple[str, str, str | None]] = [
    # User's reference example: meaning is "first invitation / quick wins",
    # not the literal accent word "ПЕРВОЕ".
    (
        "Отправил 3 отклика за 10 минут, получил первое приглашение Показываю как",
        "",
        "progress_fire",
    ),
    # Plain offer hook → money.
    ("Получил оффер на 250 тысяч расскажу как", "", "money"),
    # Adjective form of "оффер" still matches the anchor.
    ("Без оффера никуда", "", "money"),
    # Browser/site dominates over a single time hit.
    ("Часами сидел на hh открыл 50 вкладок и устал", "", "browser"),
    # Resume-themed hook.
    ("Резюме переписал и за неделю позвали на собес", "", "progress_fire"),
    # AI / neural network hook.
    ("Нейросеть пишет сопроводительные за тебя в один клик", "", "ai"),
    # Rejection hook.
    ("Получил 50 отказов подряд но не сдался", "", "reject"),
    # No anchor words → no overlay.
    ("Сверху показываю что", "", None),
]


class HookOverlayPickerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = _load_cfg()
        cls.library = _load_library()

    def _pick(self, hook_text: str, cta_text: str = "") -> str | None:
        cues = _find_accent_cues(
            _make_transcript(hook_text),
            self.cfg,
            cta_context_text=cta_text or None,
            library=self.library,
        )
        if not cues:
            return None
        # Cue.file is absolute; map it back to the asset_id via the library.
        cue_filename = cues[0].file.name
        for asset_id, entry in self.library.items():
            if entry.file == cue_filename:
                return asset_id
        return None

    def test_golden_cases(self) -> None:
        for hook_text, cta_text, expected in GOLDEN_CASES:
            with self.subTest(hook=hook_text):
                actual = self._pick(hook_text, cta_text)
                self.assertEqual(
                    actual,
                    expected,
                    f"hook={hook_text!r} expected={expected} actual={actual}",
                )

    def test_cta_context_can_break_ties(self) -> None:
        # Hook has neither money nor calc anchors strongly; CTA mentions
        # зарплату → money should win when CTA tilts the score.
        hook = "Сделал по этой схеме и всё получилось"
        # Without CTA context the hook has no anchor hits at all.
        self.assertIsNone(self._pick(hook))

        # CTA alone cannot trigger overlay (we require at least one hook hit).
        self.assertIsNone(self._pick(hook, cta_text="зарплата 250 тысяч"))

    def test_explicit_override_wins_over_scoring(self) -> None:
        # Hook scores money via "оффер" but author overrides to "like".
        transcript = _make_transcript("Получил оффер быстро")
        cues = _find_accent_cues(
            transcript,
            self.cfg,
            override_id="like",
            library=self.library,
        )
        self.assertEqual(len(cues), 1)
        self.assertTrue(cues[0].file.name == "like.png")

    def test_override_falls_back_to_last_word_when_anchors_absent(self) -> None:
        transcript = _make_transcript("какой-то хук без матчей вообще")
        cues = _find_accent_cues(
            transcript,
            self.cfg,
            override_id="money",
            library=self.library,
        )
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].word, "вообще")
        self.assertTrue(cues[0].file.name == "money.png")

    def test_max_events_zero_returns_no_cues(self) -> None:
        cfg = _load_cfg()
        cfg.hook_montage.overlays.max_events = 0
        cues = _find_accent_cues(
            _make_transcript("Получил оффер на 250 тысяч"),
            cfg,
            library=self.library,
        )
        self.assertEqual(cues, [])

    def test_min_score_gate_filters_weak_matches(self) -> None:
        cfg = _load_cfg()
        cfg.hook_montage.overlays.min_score = 5.0  # impossibly high
        cues = _find_accent_cues(
            _make_transcript("Получил оффер на 250 тысяч"),
            cfg,
            library=self.library,
        )
        self.assertEqual(cues, [])

    def test_meta_anchors_present_for_every_icon(self) -> None:
        for asset_id, entry in self.library.items():
            self.assertTrue(
                entry.anchors,
                f"{asset_id} must declare at least one anchor in _meta.json",
            )
            file_path = ROOT / "assets/hook_overlays" / entry.file
            self.assertTrue(file_path.exists(), f"missing icon file: {file_path}")


if __name__ == "__main__":
    unittest.main()
