import pytest
from src.shnurok.structure import split_hook_cta


def test_split_hook_cta_by_gap():
    words = [(8.4, 9.0, "надоели"), (9.0, 9.9, "кроссовки"), (9.9, 11.1, "машины"),
             # big silent gap
             (15.1, 15.5, "без"), (15.5, 16.6, "лейблов"), (18.8, 19.9, "стиль")]
    hook, cta = split_hook_cta(words, gap_thresh=1.5)
    assert hook[0] == 8.4 and hook[1] == 11.1
    assert cta[0] == 15.1 and cta[1] == 19.9


def test_split_hook_cta_merges_cta_internal_pauses():
    # talking-head = hook (one run) + CTA (two runs, internal pause) — NO body in this audio
    words = [(0.5, 1.0, "надоели"), (1.0, 1.5, "кроссовки"), (1.5, 2.2, "машины"),
             # big gap hook->cta
             (5.0, 5.4, "без"), (5.4, 6.0, "лейблов"),
             # internal CTA pause (>1.5s)
             (7.6, 8.0, "просто"), (8.0, 8.6, "стиль")]
    hook, cta = split_hook_cta(words, gap_thresh=1.5)
    assert hook == (0.5, 2.2)
    assert cta == (5.0, 8.6)      # merge-tail: both CTA runs, not just the last


def test_split_hook_cta_empty_raises():
    with pytest.raises(ValueError):
        split_hook_cta([])


def test_split_hook_cta_single_run():
    words = [(0.0, 0.4, "a"), (0.5, 0.9, "b"), (1.0, 1.4, "c")]  # all gaps < 1.5
    hook, cta = split_hook_cta(words, gap_thresh=1.5)
    assert hook == (0.0, 1.4) and cta == (0.0, 1.4)
