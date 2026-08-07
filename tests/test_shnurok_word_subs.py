import re
from src.shnurok.style import load_style
from src.shnurok.word_subs import word_subs_ass

def _events(txt):
    return [l for l in txt.splitlines() if l.startswith("Dialogue:")]

def _end(line):  # H:MM:SS.cc -> seconds
    t = line.split(",")[2]; h, m, s = t.split(":"); return int(h)*3600 + int(m)*60 + float(s)
def _start(line):
    t = line.split(",")[1]; h, m, s = t.split(":"); return int(h)*3600 + int(m)*60 + float(s)

def test_no_overlap(tmp_path):
    s = load_style("classic")
    words = [(5.00, 5.42, "Это"), (5.32, 5.74, "реально"), (5.64, 6.04, "уже")]
    out = tmp_path / "w.ass"
    n = word_subs_ass(words, s, out)
    assert n == 3
    evs = _events(out.read_text(encoding="utf-8"))
    for a, b in zip(evs, evs[1:]):
        assert _end(a) <= _start(b) + 1e-6          # never overlaps next

def test_uppercase_and_window(tmp_path):
    s = load_style("bold")
    words = [(1.0, 1.3, "тест"), (5.0, 5.3, "надоело")]
    out = tmp_path / "w.ass"
    word_subs_ass(words, s, out, window=(4.0, 40.0))
    body = out.read_text(encoding="utf-8")
    assert "ТЕСТ" not in body                        # outside window dropped
    assert "НАДОЕЛО" in body                          # uppercased
    assert "&H2F34D5&" in body                        # accent word in bold
