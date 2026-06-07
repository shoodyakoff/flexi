from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src import assets as assets_mod
from src import broll_library
from src.assets import load_asset_meta


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        annotation=SimpleNamespace(
            normalize_new_clips=False,
            ingest_dir="assets/broll_ingest",
            motion_presets_by_shot_scale={
                "wide": ["zoom_in_soft"],
                "medium": ["zoom_in_soft"],
                "close": ["static"],
                "detail": ["static"],
            },
            default_weight=1.0,
        )
    )


class BrollLibraryScanTest(unittest.TestCase):
    def test_scan_ignores_hook_and_cta_files_in_broll_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            assets_dir = project_root / "assets"
            broll_dir = assets_dir / "broll"
            broll_dir.mkdir(parents=True)
            for filename in ("broll_80.mov", "hook_011.MOV", "cta_011.MOV"):
                (broll_dir / filename).write_bytes(b"fake video")

            with (
                patch.object(assets_mod, "ASSETS_DIR", assets_dir),
                patch.object(broll_library, "ASSETS_DIR", assets_dir),
                patch.object(broll_library, "PROJECT_ROOT", project_root),
                patch.object(broll_library, "_ffprobe_duration", return_value=1.25),
            ):
                summary = broll_library.scan_broll_library(_cfg(), no_ingest=True)
                meta = load_asset_meta("broll")

            self.assertEqual(summary.scanned, 1)
            self.assertEqual(set(meta), {"broll_80"})


if __name__ == "__main__":
    unittest.main()
