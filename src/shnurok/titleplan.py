from __future__ import annotations

_STRIP = ",.!?…—"
PLAY_W = 1080

# Approx glyph advance for heavy uppercase Cyrillic, as a fraction of font size.
# Deliberately generous so lines never overrun the frame (better to break early
# than to clip). Used only to decide line breaks / clamp x — not for rendering.
_CHAR_K = 0.62


def _est_width(text: str, font_size: int, k: float = _CHAR_K) -> float:
    return len(text) * k * font_size


def _line_text(line) -> str:
    return " ".join(w[2].strip(_STRIP) for w in line if w[2].strip(_STRIP)).upper()


def _chunk_by_width(words, font_size: int, max_width: float, gap: float = 1.0):
    """Group words into lines that fit `max_width` when rendered at `font_size`.
    A line also breaks on a long silence gap (a new spoken beat). Punctuation-only
    tokens are dropped. Always at least one word per line."""
    lines, cur = [], []
    for s, e, w in words:
        tok = w.strip(_STRIP)
        if not tok:
            continue
        if cur:
            gap_break = s - cur[-1][1] > gap
            candidate = " ".join(x[2].strip(_STRIP) for x in (cur + [(s, e, tok)])).upper()
            width_break = _est_width(candidate, font_size) > max_width
            if gap_break or width_break:
                lines.append(cur); cur = []
        cur.append((s, e, tok))
    if cur:
        lines.append(cur)
    return lines


def hook_lines_from_words(words, font_size: int = 126, step: int = 108,
                          start_x: int = 120, start_y: int = 300,
                          x_stride: int = 70, margin: int = 40):
    """Word-level (start,end,text) hook timings -> staircase title lines
    (start, end, TEXT, x, y). Lines are broken to fit the frame width, cascade
    right/down, and x is clamped so the right edge never crosses the frame
    (no clipping). Each line stays up until the last hook word ends."""
    if not words:
        return []
    max_width = PLAY_W - start_x - margin
    lines = _chunk_by_width(words, font_size, max_width)
    t_end = words[-1][1]
    out = []
    for i, line in enumerate(lines):
        text = _line_text(line)
        if not text:
            continue
        w = _est_width(text, font_size)
        x = min(start_x + i * x_stride, int(PLAY_W - margin - w))
        x = max(margin, x)
        out.append((line[0][0], t_end, text, x, start_y + i * step))
    return out


def cta_screens_from_words(words, font_size: int = 104, step: int = 108,
                           per_screen_lines: int = 3, start_y: int = 200,
                           right_x: int = 980, margin: int = 30):
    """Word-level CTA timings -> list of screens; each screen a list of
    right-aligned (start, end, TEXT, y) lines sharing the screen's end time
    (they clear together). Lines are broken to fit between `margin` and
    `right_x`. Screens advance as the speaker moves on."""
    if not words:
        return []
    max_width = right_x - margin
    lines = _chunk_by_width(words, font_size, max_width)
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
