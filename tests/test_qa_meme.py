from pipelines.qa_meme import check_format, check_seam


def test_check_format_flags_wrong_size_and_missing_audio() -> None:
    assert check_format(1080, 1920, 30.0, True) == []
    fails = check_format(720, 1280, 30.0, False)
    assert any("1080x1920" in f for f in fails)
    assert any("audio" in f.lower() for f in fails)


def test_check_format_accepts_near_30fps() -> None:
    assert check_format(1080, 1920, 29.97, True) == []


def test_check_seam_flags_scene_a_longer_than_drop() -> None:
    assert check_seam(total_dur=6.0, scene_a_len=3.4, drop_at=3.4) == []
    assert check_seam(total_dur=6.0, scene_a_len=4.2, drop_at=3.4)  # непусто -> FAIL
