from __future__ import annotations

_STRIP = ",.!?…—"
PLAY_W = 1080

# Approx glyph advance for heavy uppercase Cyrillic, as a fraction of font size.
# Deliberately generous so lines never overrun the frame (better to break early
# than to clip). Used only to decide line breaks / clamp x — not for rendering.
_CHAR_K = 0.62


def _est_width(text: str, font_size: int, k: float = _CHAR_K) -> float:
    return len(text) * k * font_size


def _clean(tok: str) -> str:
    """Token text with surrounding whitespace AND edge punctuation removed
    (transcript tokens carry leading spaces and trailing commas/periods)."""
    return tok.strip().strip(_STRIP).strip()


def _line_text(line) -> str:
    return " ".join(t for t in (_clean(w[2]) for w in line) if t).upper()


_PUNCT_END = tuple(",.!?…;:")


def _merge_hyphen(words):
    """Whisper splits «кому-то» into «кому» + «-то»; glue such continuation
    tokens back onto the previous word so line breaks never fall inside them."""
    out = []
    for s, e, w in words:
        t = w.strip()
        if out and t.startswith("-"):
            ps, pe, pw = out[-1]
            out[-1] = (ps, e, pw.rstrip() + t)
        else:
            out.append((s, e, w))
    return out


def _split_by_punct(words):
    """Split a word stream into screens at sentence-part boundaries — a token
    whose raw text ends in ,.!?…;: closes the current screen. This is what puts
    e.g. «без попыток …» (after «лейблов,») onto its own screen."""
    groups, cur = [], []
    for s, e, w in words:
        cur.append((s, e, w))
        if w.rstrip().endswith(_PUNCT_END):
            groups.append(cur); cur = []
    if cur:
        groups.append(cur)
    return groups


def plan_screens(words, font_size: int, step: int = 108, start_x: int = 120,
                 start_y: int = 300, x_stride: int = 70, margin: int = 40, tail: float = 0.5):
    """Unified title planner for BOTH hook and CTA: split words into semantic
    screens (by punctuation), width-chunk each screen into left staircase lines
    (an7-style, same look as the hook), clamp x inside the frame. Returns a list
    of screens; each screen is a list of (start, end, TEXT, x, y). Lines of a
    screen appear at their own word start and clear together when the next
    screen begins (last screen holds `tail` seconds past its last word)."""
    words = _merge_hyphen(words)
    if not words:
        return []
    groups = _split_by_punct(words)
    starts = [g[0][0] for g in groups]
    last_end = words[-1][1]
    max_width = PLAY_W - start_x - margin
    screens = []
    for gi, g in enumerate(groups):
        screen_end = starts[gi + 1] if gi + 1 < len(groups) else last_end + tail
        out = []
        for i, line in enumerate(_chunk_by_width(g, font_size, max_width)):
            text = _line_text(line)
            if not text:
                continue
            w = _est_width(text, font_size)
            x = max(margin, min(start_x + i * x_stride, int(PLAY_W - margin - w)))
            out.append((line[0][0], screen_end, text, x, start_y + i * step))
        if out:
            screens.append(out)
    return screens


def _chunk_by_width(words, font_size: int, max_width: float, gap: float = 1.0):
    """Group words into lines that fit `max_width` when rendered at `font_size`.
    A line also breaks on a long silence gap (a new spoken beat). Punctuation-only
    tokens are dropped. Always at least one word per line."""
    lines, cur = [], []
    for s, e, w in words:
        tok = _clean(w)
        if not tok:
            continue
        if cur:
            gap_break = s - cur[-1][1] > gap
            candidate = " ".join(_clean(x[2]) for x in (cur + [(s, e, tok)])).upper()
            width_break = _est_width(candidate, font_size) > max_width
            if gap_break or width_break:
                lines.append(cur); cur = []
        cur.append((s, e, tok))
    if cur:
        lines.append(cur)
    return lines
