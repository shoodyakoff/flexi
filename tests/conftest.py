"""Test gating for the public repository.

Most of the suite is pure-logic and runs on a fresh clone. A small set of
*integration* contract tests need local content that is intentionally NOT
shipped publicly:

* real hook / CTA / b-roll media under ``assets/`` (videos, audio),
* example ``VideoScript`` JSON under ``scripts/`` (your own content), and
* overlay icon PNGs under ``assets/hook_overlays/``.

On a fresh clone that content is absent, so the tests below skip automatically.
Drop your own media + scripts into ``assets/`` and ``scripts/`` and they run in
full — the skip conditions detect the content and stop skipping.
"""

from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _has_hook_media() -> bool:
    """True once real hook/CTA clips are present (user supplied their media)."""
    for sub in ("hooks", "ctas"):
        directory = ROOT / "assets" / sub
        if directory.is_dir() and any(
            p.suffix.lower() in {".mp4", ".mov", ".m4v"} for p in directory.iterdir()
        ):
            return True
    return False


def _has_local_scripts() -> bool:
    """True once the user has added their own VideoScript JSON (beyond the example)."""
    directory = ROOT / "scripts"
    return directory.is_dir() and any(
        p.suffix == ".json" and p.name != "example.json" for p in directory.iterdir()
    )


def _has_overlay_icons() -> bool:
    directory = ROOT / "assets" / "hook_overlays"
    return directory.is_dir() and any(directory.glob("*.png"))


# Contract tests that load real VideoScript JSON + hook/CTA media as fixtures.
_NEEDS_CONTENT = {
    "test_hook_cta_transcription_uses_configured_whisper_model",
    "test_run_build_allows_raw_long_cta_when_normalized_cta_fits",
    "test_run_build_stops_before_broll_when_hook_guardrail_fails",
    "test_broll_flash_applies_every_other_cut",
    "test_hook_accent_keywords_create_text_fallback_without_overlay_anchor",
    "test_hook_motion_filter_uses_center_locked_zoompan",
    "test_keyword_overlay_and_riser_peak_sync",
    "test_long_hook_preserves_natural_pause_and_tail_padding",
    "test_normalize_cta_clip_ducks_voice_and_boosts_swoosh",
    "test_normalize_cta_clip_preserves_speech_past_target",
}

# Tests that need the overlay icon PNG files on disk (not just _meta.json).
_NEEDS_ICONS = {"test_meta_anchors_present_for_every_icon"}


def pytest_collection_modifyitems(config, items):
    content_ok = _has_hook_media() and _has_local_scripts()
    icons_ok = _has_overlay_icons()
    if content_ok and icons_ok:
        return
    skip_content = pytest.mark.skip(
        reason="integration test: needs local hook/CTA media + example scripts "
        "(not shipped in the public repo — add your own to assets/ and scripts/)"
    )
    skip_icons = pytest.mark.skip(
        reason="needs overlay icon PNGs in assets/hook_overlays/ "
        "(only _meta.json ships; add your own icons to enable)"
    )
    for item in items:
        method = item.name.split("[")[0]
        if not content_ok and method in _NEEDS_CONTENT:
            item.add_marker(skip_content)
        if not icons_ok and method in _NEEDS_ICONS:
            item.add_marker(skip_icons)
