from src.shnurok.style import load_style


def test_classic_defaults():
    s = load_style("classic")
    assert s.style_id == "classic"
    assert s.title_font == "Gilroy Heavy"
    assert s.word_font == "Gilroy Bold"
    assert s.use_accent is False
    assert s.uppercase_words is False
    assert s.hook_step == 108
    assert s.graphic_width == 330


def test_bold_overrides():
    s = load_style("bold")
    assert s.word_font == "Gilroy Heavy"
    assert s.use_accent is True
    assert s.uppercase_words is True
    assert s.transitions == "flash_punch"
    assert s.accent_bgr == "&H2F34D5&"


def test_unknown_style_raises():
    import pytest
    with pytest.raises(KeyError):
        load_style("nope")
