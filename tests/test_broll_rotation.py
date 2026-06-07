from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src.assets import (
    load_broll_recent_history,
    load_usage_counts,
    save_broll_recent_history,
    save_usage_counts,
)
from src.broll_picker import _candidate_score
from src.schemas import AssetEntry


def _dummy_entry(**kwargs) -> AssetEntry:
    defaults = dict(
        file="broll_1.mov",
        duration=5.0,
        shot_scale="medium",
        sequence_role="action",
        energy=2,
        scene_group="office",
        subject_kind="people",
    )
    defaults.update(kwargs)
    return AssetEntry(**defaults)


class UsageCountsIOTest(unittest.TestCase):
    def test_load_missing_returns_empty(self, tmp_path=None) -> None:
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "no_assets" / "broll" / "_usage.json"
            from src import assets as assets_mod
            original = assets_mod._broll_usage_path
            assets_mod._broll_usage_path = lambda: missing
            try:
                result = load_usage_counts()
                self.assertEqual(result, {})
            finally:
                assets_mod._broll_usage_path = original

    def test_roundtrip(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            usage_path = Path(d) / "_usage.json"
            from src import assets as assets_mod
            original = assets_mod._broll_usage_path
            assets_mod._broll_usage_path = lambda: usage_path
            try:
                counts = {"broll_1": 3, "broll_5": 7}
                save_usage_counts(counts)
                loaded = load_usage_counts()
                self.assertEqual(loaded, counts)
            finally:
                assets_mod._broll_usage_path = original

    def test_save_creates_parent_dirs(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            usage_path = Path(d) / "nested" / "deep" / "_usage.json"
            from src import assets as assets_mod
            original = assets_mod._broll_usage_path
            assets_mod._broll_usage_path = lambda: usage_path
            try:
                save_usage_counts({"broll_2": 1})
                self.assertTrue(usage_path.exists())
            finally:
                assets_mod._broll_usage_path = original


class RecentHistoryIOTest(unittest.TestCase):
    def test_missing_recent_history_returns_empty(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            history_path = Path(d) / "_recent_history.json"
            from src import assets as assets_mod
            original = assets_mod._broll_recent_history_path
            assets_mod._broll_recent_history_path = lambda: history_path
            try:
                self.assertEqual(load_broll_recent_history(), [])
            finally:
                assets_mod._broll_recent_history_path = original

    def test_recent_history_roundtrip(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            history_path = Path(d) / "_recent_history.json"
            from src import assets as assets_mod
            original = assets_mod._broll_recent_history_path
            assets_mod._broll_recent_history_path = lambda: history_path
            try:
                payload = [["broll", "broll_68", "broll_7"]]
                save_broll_recent_history(payload)
                self.assertEqual(load_broll_recent_history(), payload)
            finally:
                assets_mod._broll_recent_history_path = original


class GlobalPenaltyTest(unittest.TestCase):
    def _score(self, asset_id: str, global_use_counts: dict[str, int]) -> float:
        entry = _dummy_entry()
        with patch("src.broll_picker.random") as mock_random:
            mock_random.gauss.return_value = 0.0
            return _candidate_score(
                asset_id,
                entry,
                desired_block=None,
                desired_scales=("medium",),
                desired_role="action",
                history=[],
                soft_mode=True,
                global_use_counts=global_use_counts,
            )

    def test_unused_asset_scores_higher_than_used(self) -> None:
        score_fresh = self._score("broll_new", {})
        score_used = self._score("broll_old", {"broll_old": 5})
        self.assertGreater(score_fresh, score_used)

    def test_penalty_saturates_at_6(self) -> None:
        score_8 = self._score("a", {"a": 8})
        score_100 = self._score("a", {"a": 100})
        self.assertAlmostEqual(score_8, score_100, places=5)

    def test_zero_global_count_no_penalty(self) -> None:
        score_none = self._score("a", {})
        score_zero = self._score("a", {"a": 0})
        self.assertAlmostEqual(score_none, score_zero, places=5)

    def test_noise_applied(self) -> None:
        entry = _dummy_entry()
        results = set()
        for _ in range(20):
            s = _candidate_score(
                "broll_1",
                entry,
                desired_block=None,
                desired_scales=("medium",),
                desired_role="action",
                history=[],
                soft_mode=True,
                global_use_counts={},
            )
            results.add(round(s, 6))
        self.assertGreater(len(results), 1, "gauss noise should produce different scores each call")


class RecentPenaltyTest(unittest.TestCase):
    def test_recent_start_asset_is_penalized_more_than_global_count(self) -> None:
        fresh = _dummy_entry(scene_group="fresh")
        repeated = _dummy_entry(scene_group="repeated")

        with patch("src.broll_picker.random") as mock_random:
            mock_random.gauss.return_value = 0.0
            fresh_score = _candidate_score(
                "fresh",
                fresh,
                desired_block="explain",
                desired_scales=("medium",),
                desired_role="action",
                history=[],
                soft_mode=False,
                global_use_counts={"fresh": 100},
                recent_start_assets={"repeated"},
                recent_anywhere_assets=set(),
                recent_start_penalty=18.0,
                recent_anywhere_penalty=6.0,
            )
            repeated_score = _candidate_score(
                "repeated",
                repeated,
                desired_block="explain",
                desired_scales=("medium",),
                desired_role="action",
                history=[],
                soft_mode=False,
                global_use_counts={},
                recent_start_assets={"repeated"},
                recent_anywhere_assets=set(),
                recent_start_penalty=18.0,
                recent_anywhere_penalty=6.0,
            )

        self.assertGreater(fresh_score, repeated_score)

    def test_recent_anywhere_asset_gets_separate_smaller_penalty(self) -> None:
        start = _dummy_entry(scene_group="start")
        anywhere = _dummy_entry(scene_group="anywhere")

        with patch("src.broll_picker.random") as mock_random:
            mock_random.gauss.return_value = 0.0
            start_score = _candidate_score(
                "start",
                start,
                desired_block="explain",
                desired_scales=("medium",),
                desired_role="action",
                history=[],
                soft_mode=False,
                global_use_counts={},
                recent_start_assets={"start"},
                recent_anywhere_assets={"start", "anywhere"},
                recent_start_penalty=18.0,
                recent_anywhere_penalty=6.0,
            )
            anywhere_score = _candidate_score(
                "anywhere",
                anywhere,
                desired_block="explain",
                desired_scales=("medium",),
                desired_role="action",
                history=[],
                soft_mode=False,
                global_use_counts={},
                recent_start_assets={"start"},
                recent_anywhere_assets={"start", "anywhere"},
                recent_start_penalty=18.0,
                recent_anywhere_penalty=6.0,
            )

        self.assertGreater(anywhere_score, start_score)


if __name__ == "__main__":
    unittest.main()
