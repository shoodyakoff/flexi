from pathlib import Path

import pipelines.qa_meme as qa_meme
from pipelines.qa_meme import _probe, check_format, check_seam


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


def test_probe_parses_default_format_without_side_data(monkeypatch) -> None:
    # ffprobe -of default=nokey=1 отдаёт чистые строки без хвостового side_data-поля
    outputs = iter(["1080\n1920", "30/1", "1"])  # (width,height), r_frame_rate, audio index

    class _Result:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def _fake_run(*args, **kwargs) -> _Result:
        return _Result(next(outputs))

    monkeypatch.setattr(qa_meme.subprocess, "run", _fake_run)
    assert _probe(Path("x.mp4")) == (1080, 1920, 30.0, True)
