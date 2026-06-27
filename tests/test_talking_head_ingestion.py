"""Clip ingestion hardening for route #2: --clips-dir, keep-full, retake warning."""
import io
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from pipelines.render_talking_head_dynamic_clean import (
    SpeechSegment,
    resolve_input_paths,
    warn_on_large_retake_drops,
)


def _args(input=None, clips_dir=None):
    return Namespace(input=input or [], clips_dir=clips_dir)


def test_clips_dir_ingests_sorted_video_files():
    with TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "IMG_03.MOV").write_bytes(b"x")
        (d / "IMG_01.MOV").write_bytes(b"x")
        (d / "IMG_02.mov").write_bytes(b"x")
        (d / "notes.txt").write_text("ignore me")
        (d / "cover.jpg").write_bytes(b"x")
        paths = resolve_input_paths(_args(clips_dir=d))
        assert [p.name for p in paths] == ["IMG_01.MOV", "IMG_02.mov", "IMG_03.MOV"]


def test_explicit_inputs_precede_clips_dir():
    with TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "b.mov").write_bytes(b"x")
        explicit = d / "a.mov"
        explicit.write_bytes(b"x")
        paths = resolve_input_paths(_args(input=[explicit], clips_dir=d))
        # explicit input first, then folder contents
        assert paths[0].name == "a.mov"
        assert "b.mov" in [p.name for p in paths]


def test_no_inputs_raises():
    with pytest.raises(SystemExit):
        resolve_input_paths(_args())


def test_retake_warning_fires_on_large_contiguous_drop():
    sources = [Path("clip.MOV")]
    before = [SpeechSegment(0, 0.0, 30.0)]
    after = [SpeechSegment(0, 0.0, 2.0), SpeechSegment(0, 17.0, 30.0)]  # 15s dropped
    out = io.StringIO()
    with redirect_stdout(out):
        warn_on_large_retake_drops(before, after, sources)
    text = out.getvalue()
    assert "retake removal dropped" in text
    assert "13.0s" in text or "15.0s" in text


def test_retake_warning_silent_on_small_trims():
    sources = [Path("clip.MOV")]
    before = [SpeechSegment(0, 0.0, 30.0)]
    after = [SpeechSegment(0, 0.0, 29.0)]  # 1s tail trim, below threshold
    out = io.StringIO()
    with redirect_stdout(out):
        warn_on_large_retake_drops(before, after, sources)
    assert out.getvalue().strip() == ""


def _title_args(title=None, auto_title=False):
    return Namespace(title=title, auto_title=auto_title)


class _TitleOverlayCfg:
    def __init__(self, default_title):
        self.default_title = default_title


class _Cfg:
    def __init__(self, default_title):
        self.title_overlay = _TitleOverlayCfg(default_title)


def test_explicit_title_wins_over_auto():
    from pipelines.render_talking_head_dynamic_clean import resolve_title_clip

    cfg = _Cfg("assets/titles/title_001.mp4")
    result = resolve_title_clip(_title_args(title=Path("assets/titles/custom.mp4"), auto_title=True), cfg)
    assert result is not None and result.name == "custom.mp4"


def test_auto_title_uses_config_default():
    from pipelines.render_talking_head_dynamic_clean import resolve_title_clip

    cfg = _Cfg("assets/titles/title_001.mp4")
    result = resolve_title_clip(_title_args(auto_title=True), cfg)
    assert result is not None and result.name == "title_001.mp4"


def test_no_title_and_no_auto_returns_none():
    from pipelines.render_talking_head_dynamic_clean import resolve_title_clip

    cfg = _Cfg("assets/titles/title_001.mp4")
    assert resolve_title_clip(_title_args(), cfg) is None


def test_auto_title_without_default_returns_none():
    from pipelines.render_talking_head_dynamic_clean import resolve_title_clip

    cfg = _Cfg(None)
    assert resolve_title_clip(_title_args(auto_title=True), cfg) is None
