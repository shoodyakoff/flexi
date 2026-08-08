from src.shnurok.titleplan import (
    hook_lines_from_words,
    cta_screens_from_words,
    _est_width,
    PLAY_W,
)


def test_hook_lines_break_by_width_and_never_clip():
    # A wide two-word phrase must split into separate staircase lines (this is the
    # regression the width-based chunker fixes: "НАДОЕЛИ КРОССОВКИ" would clip).
    words = [(0.0, 0.5, "надоели"), (0.6, 1.1, "кроссовки"), (1.2, 1.4, "по"),
             (1.4, 1.7, "цене"), (2.0, 2.7, "машины?")]
    fs = 126
    lines = hook_lines_from_words(words, font_size=fs, start_x=120, x_stride=70, margin=40)
    texts = [l[2] for l in lines]
    assert "НАДОЕЛИ" in texts and "КРОССОВКИ" in texts   # split, not "НАДОЕЛИ КРОССОВКИ"
    assert "НАДОЕЛИ КРОССОВКИ" not in texts
    assert "ПО ЦЕНЕ" in texts                             # short pair still groups
    # nothing runs off the right edge, and text is uppercased, held to last word end
    for _s, e, text, x, _y in lines:
        assert x + _est_width(text, fs) <= PLAY_W - 40 + 1
        assert x >= 40
        assert text == text.upper()
        assert e == 2.7


def test_hook_lines_empty():
    assert hook_lines_from_words([]) == []


def test_cta_screens_grouping_shared_end_and_fit():
    words = [(0.0, 0.4, "без"), (0.4, 1.0, "надоедливых"), (1.0, 1.4, "лейблов"),
             (1.7, 1.8, "без"), (1.8, 2.3, "попыток"),
             (2.5, 2.8, "просто"), (2.8, 3.2, "стиль")]
    fs = 104
    screens = cta_screens_from_words(words, font_size=fs, per_screen_lines=3,
                                     start_y=200, right_x=980, margin=30)
    assert len(screens) >= 1
    # lines within a screen share one end time (clear together) + fit left of anchor
    for screen in screens:
        assert len({ln[1] for ln in screen}) == 1
        for _s, _e, text, _y in screen:
            assert _est_width(text, fs) <= 980 - 30 + 1
            assert text == text.upper()
    # non-final screen ends exactly when the next screen starts
    if len(screens) >= 2:
        assert screens[0][0][1] == screens[1][0][0]
    # last screen ends at the last word's end
    assert screens[-1][-1][1] == 3.2


def test_cta_screens_empty():
    assert cta_screens_from_words([]) == []
