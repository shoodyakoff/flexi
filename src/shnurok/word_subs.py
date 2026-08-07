from __future__ import annotations
from pathlib import Path
from .titles import ass_ts, is_accent

SOFTW = r"\bord3\blur4\3c&H141414&\3a&H50&\shad0"
POPW  = r"\fscx82\fscy82\t(0,70,\fscx100\fscy100)"

def _header(font: str, size: int) -> str:
    return (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: W,{font},{size},&H00FFFFFF,&H000000FF,&H00101010,&H00000000,0,0,0,0,100,100,1,0,1,0,0,5,0,0,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

def word_subs_ass(words, style, out_path, pos=(540, 940), window=None):
    items = []
    for s, e, w in words:
        cl = w.strip(",.!?…—")
        if not cl:
            continue
        if window and not (window[0] <= s < window[1]):
            continue
        items.append((s, e, cl))
    items.sort(key=lambda x: x[0])
    x, y = pos
    ev = []
    for i, (s, e, cl) in enumerate(items):
        nxt = items[i + 1][0] if i + 1 < len(items) else (e + 0.40)
        end = min(e + 0.40, nxt - 0.02)
        if end <= s + 0.05:
            end = min(s + 0.12, nxt - 0.01)
        disp = cl.upper() if style.uppercase_words else cl
        tags = f"\\pos({x},{y}){SOFTW}{POPW}"
        if style.use_accent and is_accent(cl):
            tags += f"\\c{style.accent_bgr}"
        ev.append(f"Dialogue: 2,{ass_ts(s)},{ass_ts(end)},W,,0,0,0,,{{{tags}}}{disp}")
    Path(out_path).write_text(_header(style.word_font, style.word_size) + "\n".join(ev) + "\n", encoding="utf-8")
    return len(ev)
