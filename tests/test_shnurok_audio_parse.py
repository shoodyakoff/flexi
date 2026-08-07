import shutil, pytest
from pathlib import Path
from src.shnurok.audio_mix import parse_ebur128_i, gain_db

def test_parse_ebur128_i():
    sample = "[Parsed_ebur128_0 @ 0x1] Summary:\n  Integrated loudness:\n    I:         -23.7 LUFS\n"
    assert parse_ebur128_i(sample) == -23.7

def test_gain_db():
    assert gain_db(-23.7, -16.0) == pytest.approx(7.7, abs=0.01)
