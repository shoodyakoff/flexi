from __future__ import annotations
from pathlib import Path

SOFT = r"\bord5\blur6\3c&H141414&\3a&H55&\shad0"
POP  = r"\fscx84\fscy84\t(0,80,\fscx100\fscy100)"
ACCENT_WORDS = {"машины?", "машины", "стиль", "надоело", "конец", "дорого", "дешевле", "balenciaga"}

def ass_ts(t: float) -> str:
    t = max(0.0, t); h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{int(s):02d}.{min(99, round((s - int(s)) * 100)):02d}"

def is_accent(text: str) -> bool:
    return text.strip().lower() in ACCENT_WORDS

def _header(font: str, size: int) -> str:
    return (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: T,{font},{size},&H00FFFFFF,&H000000FF,&H00101010,&H00000000,0,0,0,0,100,100,2,0,1,0,0,7,0,0,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

def _line(t0, t1, text, x, y, style, an, fs=None, tilt=0.0):
    tags = f"\\an{an}\\pos({x},{y}){SOFT}{POP}"
    if fs:
        tags += f"\\fs{fs}"
    if style.tilt and tilt:
        tags += f"\\frz{tilt}"
    if style.use_accent and is_accent(text):
        tags += style.accent_bgr and f"\\c{style.accent_bgr}" or ""
    return f"Dialogue: 1,{ass_ts(t0)},{ass_ts(t1)},T,,0,0,0,,{{{tags}}}{text}"

def hook_titles_ass(front_lines, behind, style, out_path):
    out_path = Path(out_path)
    ev = [_line(t0, t1, txt, x, y, style, an=7, tilt=(-1.5 if i % 2 else 1.5))
          for i, (t0, t1, txt, x, y) in enumerate(front_lines)]
    out_path.write_text(_header(style.title_font, style.hook_size) + "\n".join(ev) + "\n", encoding="utf-8")
    if behind is not None:
        t0, t1, txt, x, y = behind
        b = _line(t0, t1, txt, x, y, style, an=7, fs=style.behind_fontsize, tilt=1.5)
        out_path.with_suffix(".behind.ass").write_text(
            _header(style.title_font, style.hook_size) + b + "\n", encoding="utf-8")

def cta_titles_ass(screens, style, out_path):
    ev = []
    for screen in screens:
        for i, (t0, t1, txt, y) in enumerate(screen):
            ev.append(_line(t0, t1, txt, 980, y, style, an=9, tilt=(-1.2 if i % 2 else 1.2)))
    Path(out_path).write_text(_header(style.title_font, style.cta_size) + "\n".join(ev) + "\n", encoding="utf-8")
