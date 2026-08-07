# -*- coding: utf-8 -*-
"""Unit tests for shnurok QA gates: word-sub overlap detection."""
from pathlib import Path
from src.shnurok.style import load_style
from src.shnurok.word_subs import word_subs_ass
import importlib.util, sys

spec = importlib.util.spec_from_file_location("qa_shnurok", "pipelines/qa_shnurok.py")
qa = importlib.util.module_from_spec(spec)
sys.modules["qa_shnurok"] = qa
spec.loader.exec_module(qa)


def test_clean_word_subs_pass(tmp_path):
    """Clean (non-overlapping) word subs should pass."""
    s = load_style("classic")
    out = tmp_path / "w.ass"
    word_subs_ass([(5.0, 5.4, "а"), (5.5, 5.9, "б"), (6.0, 6.4, "в")], s, out)
    assert qa.check_single_word_subs(out) == []


def test_overlapping_subs_flagged(tmp_path):
    """Overlapping word subs should flag exactly 1 violation."""
    bad = tmp_path / "bad.ass"
    bad.write_text(
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 2,0:00:05.00,0:00:05.60,W,,0,0,0,,{}а\n"
        "Dialogue: 2,0:00:05.30,0:00:05.90,W,,0,0,0,,{}б\n",
        encoding="utf-8"
    )
    assert len(qa.check_single_word_subs(bad)) == 1
