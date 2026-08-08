from src.shnurok.titleplan import plan_screens, _est_width, PLAY_W


def test_hook_splits_screens_by_punctuation_and_never_clips():
    # "…машины?" closes screen 1; "тогда смотри!" is a second screen (matches v5).
    words = [(0.0, 0.5, " надоели"), (0.6, 1.1, " кроссовки"), (1.2, 1.4, " по"),
             (1.4, 1.7, " цене"), (2.0, 2.7, " машины?"),
             (3.4, 3.7, " тогда"), (3.8, 4.3, " смотри!")]
    fs = 126
    screens = plan_screens(words, font_size=fs, start_x=120, x_stride=70, margin=40)
    assert len(screens) == 2
    s1 = [ln[2] for ln in screens[0]]
    assert "НАДОЕЛИ" in s1 and "КРОССОВКИ" in s1 and "МАШИНЫ" in s1
    assert "НАДОЕЛИ КРОССОВКИ" not in s1               # wide pair split, no clip
    assert [ln[2] for ln in screens[1]] == ["ТОГДА", "СМОТРИ"]
    # screen 1 clears exactly when screen 2 begins
    assert screens[0][0][1] == screens[1][0][0]
    # nothing runs off the right edge; text uppercased
    for screen in screens:
        for _s, _e, text, x, _y in screen:
            assert x + _est_width(text, fs) <= PLAY_W - 40 + 1
            assert x >= 40 and text == text.upper()


def test_cta_semantic_screens_and_hyphen_merge():
    # "лейблов," closes screen 1 -> "без попыток…" starts screen 2 (user requirement);
    # "кому"+"-то" / "что"+"-то" glue back into single tokens (no ugly "-ТО" break).
    words = [(0.0, 0.4, " без"), (0.4, 1.0, " надоедливых"), (1.0, 1.4, " лейблов,"),
             (1.7, 1.8, " без"), (1.8, 2.3, " попыток"), (2.4, 2.6, " кому"), (2.6, 2.7, "-то"),
             (2.8, 3.0, " что"), (3.0, 3.1, "-то"), (3.2, 3.7, " доказать."),
             (3.9, 4.2, " просто"), (4.2, 4.6, " стиль.")]
    screens = plan_screens(words, font_size=104)
    assert len(screens) == 3
    joined = [" ".join(ln[2] for ln in sc) for sc in screens]
    assert "БЕЗ" in joined[0] and "ЛЕЙБЛОВ" in joined[0] and "ПОПЫТОК" not in joined[0]
    assert "ПОПЫТОК" in joined[1] and "КОМУ-ТО" in joined[1] and "ЧТО-ТО" in joined[1]
    assert "-ТО" not in "".join(ln[2].replace("КОМУ-ТО", "").replace("ЧТО-ТО", "") for ln in screens[1])
    assert "СТИЛЬ" in joined[2]
    # last screen holds past its last word; lines carry x,y (staircase like hook)
    assert screens[-1][-1][1] > 4.6
    assert all(len(ln) == 5 for sc in screens for ln in sc)


def test_plan_screens_empty():
    assert plan_screens([], font_size=126) == []
