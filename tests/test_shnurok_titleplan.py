from src.shnurok.titleplan import hook_lines_from_words, cta_screens_from_words

def test_hook_lines_chunk_and_positions():
    words = [(0.0, 0.5, "надоели"), (0.6, 1.1, "кроссовки"), (1.2, 1.4, "по"),
             (1.4, 1.7, "цене"), (2.0, 2.7, "машины?")]
    lines = hook_lines_from_words(words, step=108, start_x=140, start_y=300, x_stride=90)
    assert [l[2] for l in lines] == ["НАДОЕЛИ КРОССОВКИ", "ПО ЦЕНЕ", "МАШИНЫ"]
    assert all(l[1] == 2.7 for l in lines)          # all held to last word end
    assert [l[3] for l in lines] == [140, 230, 320]  # x cascades right
    assert [l[4] for l in lines] == [300, 408, 516]  # y cascades down

def test_hook_lines_empty():
    assert hook_lines_from_words([]) == []

def test_cta_screens_grouping_and_shared_end():
    words = [(0.0, 0.4, "без"), (0.4, 1.0, "надоедливых"), (1.0, 1.4, "лейблов"),
             (1.7, 1.8, "без"), (1.8, 2.3, "попыток"),
             (2.5, 2.8, "просто"), (2.8, 3.2, "стиль")]
    screens = cta_screens_from_words(words, per_screen_lines=3, start_y=200, max_words=2, gap=0.35)
    assert len(screens) == 2                          # 4 lines -> screens of 3 + 1
    # screen 1 has 3 lines, all sharing the same end (= screen 2 start)
    assert len(screens[0]) == 3
    ends1 = {ln[1] for ln in screens[0]}
    assert len(ends1) == 1
    s2_start = screens[1][0][0]
    assert next(iter(ends1)) == s2_start
    # last screen ends at last word end
    assert screens[1][-1][1] == 3.2
    # right-aligned y cascade within a screen
    assert [ln[3] for ln in screens[0]] == [200, 308, 416]

def test_cta_screens_empty():
    assert cta_screens_from_words([]) == []
