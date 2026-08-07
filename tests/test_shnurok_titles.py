from pathlib import Path
from src.shnurok.style import load_style
from src.shnurok.titles import hook_titles_ass, cta_titles_ass, ass_ts

def test_ass_ts():
    assert ass_ts(0) == "0:00:00.00"
    assert ass_ts(65.5) == "0:01:05.50"

def test_hook_writes_front_and_behind(tmp_path):
    s = load_style("bold")
    out = tmp_path / "hook.ass"
    hook_titles_ass(
        front_lines=[(0.05, 3.3, "НАДОЕЛИ", 150, 310), (3.42, 4.55, "ТОГДА", 360, 250)],
        behind=(1.54, 3.3, "МАШИНЫ?", 300, 640),
        style=s, out_path=out,
    )
    front = out.read_text(encoding="utf-8")
    behind = (tmp_path / "hook.behind.ass").read_text(encoding="utf-8")
    assert "НАДОЕЛИ" in front and "МАШИНЫ?" not in front       # behind kept separate
    assert "МАШИНЫ?" in behind
    assert "\\fs140" in behind                                  # behind uses behind_fontsize
    assert "&H2F34D5&" in behind                                # bold accents keyword
    assert "Gilroy Heavy" in front

def test_cta_lines_share_screen_end(tmp_path):
    s = load_style("classic")
    out = tmp_path / "cta.ass"
    cta_titles_ass(
        screens=[[(44.9, 47.3, "БЕЗ НАДОЕДЛИВЫХ", 200),
                  (46.1, 47.3, "ЛЕЙБЛОВ", 308),
                  (46.7, 47.3, "БЕЗ ПОПЫТОК", 416)]],
        style=s, out_path=out,
    )
    body = out.read_text(encoding="utf-8")
    assert body.count("0:00:47.30") == 3          # all three clear together
    assert "\\an9" in body and "&H2F34D5&" not in body   # right-aligned, no accent in classic
