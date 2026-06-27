"""Whole-token subtitle vocabulary corrections (config-driven)."""
from src.subtitles import apply_word_corrections, build_corrections_map


CORRECTIONS = build_corrections_map(
    {
        "цепровод": "Сопровод",
        "рилз": "рилс",
        "код": "Клод",
        "коде": "Клоде",
        "сас": "SaaS",
    }
)


def test_replaces_whole_token_case_insensitively():
    assert apply_word_corrections("код", CORRECTIONS) == "Клод"
    assert apply_word_corrections("КОД", CORRECTIONS) == "Клод"
    assert apply_word_corrections("коде", CORRECTIONS) == "Клоде"
    assert apply_word_corrections("рилз", CORRECTIONS) == "рилс"
    assert apply_word_corrections("сас", CORRECTIONS) == "SaaS"


def test_does_not_touch_substrings():
    # «кодер» (вайб-кодер) must survive — only the whole token «код» maps.
    assert apply_word_corrections("кодер", CORRECTIONS) == "кодер"
    assert apply_word_corrections("кодера", CORRECTIONS) == "кодера"
    assert apply_word_corrections("наконец-то", CORRECTIONS) == "наконец-то"


def test_preserves_leading_space_and_punctuation():
    assert apply_word_corrections(" код", CORRECTIONS) == " Клод"
    assert apply_word_corrections("код.", CORRECTIONS) == "Клод."
    assert apply_word_corrections("«код»", CORRECTIONS) == "«Клод»"
    assert apply_word_corrections("код?", CORRECTIONS) == "Клод?"


def test_unmatched_and_empty_inputs_pass_through():
    assert apply_word_corrections("видео", CORRECTIONS) == "видео"
    assert apply_word_corrections("", CORRECTIONS) == ""
    assert apply_word_corrections("код", {}) == "код"


def test_build_corrections_map_casefolds_keys():
    m = build_corrections_map({"РИЛЗ": "рилс"})
    assert apply_word_corrections("рилз", m) == "рилс"
    assert build_corrections_map(None) == {}
