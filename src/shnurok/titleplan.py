from __future__ import annotations

_STRIP = ",.!?…—"

def _chunk_by_rhythm(words, max_words: int = 2, gap: float = 0.35):
    """Group consecutive words into lines: break when a line reaches max_words
    or the silence gap before the next word exceeds `gap` seconds."""
    lines, cur = [], []
    for s, e, w in words:
        if cur and (len(cur) >= max_words or s - cur[-1][1] > gap):
            lines.append(cur); cur = []
        cur.append((s, e, w))
    if cur:
        lines.append(cur)
    return lines

def _line_text(line) -> str:
    return " ".join(w[2].strip(_STRIP) for w in line if w[2].strip(_STRIP)).upper()

def hook_lines_from_words(words, step: int = 108, start_x: int = 140,
                          start_y: int = 300, x_stride: int = 90, max_x: int = 360):
    """Word-level (start,end,text) hook timings -> staircase title lines
    (start, end, TEXT, x, y). Each line stays up until the last hook word ends;
    lines cascade right (x) and down (y)."""
    if not words:
        return []
    lines = _chunk_by_rhythm(words)
    t_end = words[-1][1]
    out = []
    for i, line in enumerate(lines):
        text = _line_text(line)
        if not text:
            continue
        x = min(start_x + i * x_stride, max_x)
        y = start_y + i * step
        out.append((line[0][0], t_end, text, x, y))
    return out

def cta_screens_from_words(words, step: int = 108, per_screen_lines: int = 3,
                           start_y: int = 200, max_words: int = 2, gap: float = 0.35):
    """Word-level CTA timings -> list of screens; each screen a list of
    right-aligned (start, end, TEXT, y) lines that share the screen's end time
    (they clear together). Screens advance as the speaker moves on."""
    if not words:
        return []
    lines = _chunk_by_rhythm(words, max_words, gap)
    groups = [lines[i:i + per_screen_lines] for i in range(0, len(lines), per_screen_lines)]
    starts = [g[0][0][0] for g in groups]
    last_end = words[-1][1]
    result = []
    for gi, g in enumerate(groups):
        s_end = starts[gi + 1] if gi + 1 < len(groups) else last_end
        screen = []
        for li, line in enumerate(g):
            text = _line_text(line)
            if not text:
                continue
            screen.append((line[0][0], s_end, text, start_y + li * step))
        if screen:
            result.append(screen)
    return result
