from src.shnurok.structure import split_hook_cta


def test_split_hook_cta_by_gap():
    words = [(8.4, 9.0, "надоели"), (9.0, 9.9, "кроссовки"), (9.9, 11.1, "машины"),
             # big silent gap
             (15.1, 15.5, "без"), (15.5, 16.6, "лейблов"), (18.8, 19.9, "стиль")]
    hook, cta = split_hook_cta(words, gap_thresh=1.5)
    assert hook[0] == 8.4 and hook[1] == 11.1
    assert cta[0] == 15.1 and cta[1] == 19.9
