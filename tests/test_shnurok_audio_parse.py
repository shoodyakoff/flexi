import shutil, subprocess
import pytest
from src.shnurok.audio_mix import parse_ebur128_i, gain_db

def test_parse_ebur128_i():
    sample = "[Parsed_ebur128_0 @ 0x1] Summary:\n  Integrated loudness:\n    I:         -23.7 LUFS\n"
    assert parse_ebur128_i(sample) == -23.7

def test_gain_db():
    assert gain_db(-23.7, -16.0) == pytest.approx(7.7, abs=0.01)

HAVE_FFMPEG = shutil.which("ffmpeg") is not None

def _sine(path, dur, freq):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"sine=frequency={freq}:duration={dur}", "-c:a", "aac", str(path)], check=True)

@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg missing")
def test_mix_honors_total_dur(tmp_path):
    from src.shnurok.style import load_style
    from src.shnurok.audio_mix import mix_voice_music
    s1, s2, mus, out = tmp_path / "s1.m4a", tmp_path / "s2.m4a", tmp_path / "m.m4a", tmp_path / "o.m4a"
    _sine(s1, 2.0, 440); _sine(s2, 1.0, 550); _sine(mus, 3.0, 220)
    style = load_style("classic")
    # segments sum to 3.0s but total_dur is 5.0 -> mixed output must be ~5.0s (music fills tail)
    mix_voice_music([(s1, 0.0, 2.0), (s2, 0.0, 1.0)], mus, out, style, total_dur=5.0)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout)
    assert dur == pytest.approx(5.0, abs=0.25)
