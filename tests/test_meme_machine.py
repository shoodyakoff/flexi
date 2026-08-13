# tests/test_meme_machine.py
from __future__ import annotations
from pathlib import Path
import json
import pytest
from src.meme_machine import (
    Beat, MemePlan, CaptionPair, load_plan, load_pairs,
    scene_a_length, audio_start,
)


def test_scene_a_length_clips_to_drop_when_face_longer() -> None:
    assert scene_a_length(clip_len=6.0, drop_at=3.4) == 3.4  # режем хвост клипа


def test_scene_a_length_uses_full_clip_when_shorter() -> None:
    assert scene_a_length(clip_len=2.5, drop_at=3.4) == 2.5


def test_audio_start_is_never_negative() -> None:
    assert audio_start(clip_len=6.0, drop_at=3.4) == pytest.approx(0.0)   # face>=drop -> full intro
    assert audio_start(clip_len=2.5, drop_at=3.4) == pytest.approx(0.9)   # face<drop -> intro trimmed


def test_load_plan_parses_two_beat_meme(tmp_path: Path) -> None:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({
        "source": "src.mp4",
        "drop_at": 3.4,
        "beats": [
            {"role": "setup", "fill": "face", "caption_pos": "top", "caption_mode": "own"},
            {"role": "punch", "fill": "source", "caption_pos": "top", "caption_mode": "keep"},
        ],
    }), encoding="utf-8")
    plan = load_plan(p)
    assert plan.drop_at == 3.4
    assert plan.source == Path("src.mp4")
    assert plan.beats[0].fill == "face"
    assert plan.beats[1].caption_mode == "keep"
    assert plan.beats[1].cover is None


def test_load_pairs_allows_missing_b(tmp_path: Path) -> None:
    p = tmp_path / "pairs.yaml"
    p.write_text(
        "- {a: \"первый сетап\", b: \"первый панч\"}\n"
        "- {a: \"второй сетап\"}\n",
        encoding="utf-8",
    )
    pairs = load_pairs(p)
    assert pairs == [CaptionPair(a="первый сетап", b="первый панч"),
                     CaptionPair(a="второй сетап", b=None)]
