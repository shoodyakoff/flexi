# tests/test_meme_machine.py
from __future__ import annotations
from pathlib import Path
import json
import pytest
from src.meme_machine import (
    Beat, MemePlan, CaptionPair, load_plan, load_pairs,
    scene_a_length, audio_start,
)
from src.meme_machine import collect_face_clips, parse_faces_subset, assign_faces
from src.meme_machine import build_face_scene_command, build_punch_scene_command


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


def test_collect_face_clips_sorts_and_ignores_non_video(tmp_path: Path) -> None:
    for name in ["2.mp4", "10.mov", "1.mp4", ".DS_Store", "note.txt", "7.mov"]:
        (tmp_path / name).write_text("x")
    files = collect_face_clips(tmp_path)
    assert [f.name for f in files] == ["1.mp4", "2.mp4", "7.mov", "10.mov"]


def test_parse_faces_subset_supports_lists_and_ranges(tmp_path: Path) -> None:
    for name in ["1.mp4", "2.mp4", "3.mp4", "5.mp4", "11.mp4", "12.mp4"]:
        (tmp_path / name).write_text("x")
    avail = collect_face_clips(tmp_path)
    picked = parse_faces_subset("1,3,11-99", avail)
    assert [f.name for f in picked] == ["1.mp4", "3.mp4", "11.mp4", "12.mp4"]


def test_assign_faces_is_deterministic_and_cycles(tmp_path: Path) -> None:
    faces = [tmp_path / f"{i}.mp4" for i in range(1, 4)]  # 3 лица
    a = assign_faces(n=5, faces=faces, seed=42)
    b = assign_faces(n=5, faces=faces, seed=42)
    assert a == b                      # повторяемо
    assert len(a) == 5                 # 5 назначений на 3 лица
    assert set(a) <= set(faces)


def test_assign_faces_changes_with_seed(tmp_path: Path) -> None:
    faces = [tmp_path / f"{i}.mp4" for i in range(1, 6)]
    assert assign_faces(5, faces, seed=1) != assign_faces(5, faces, seed=2)


def test_face_scene_uses_source_audio_window_and_face_video(tmp_path: Path) -> None:
    cmd = build_face_scene_command(
        face_path=tmp_path / "face.mp4",
        source_path=tmp_path / "src.mp4",
        output_path=tmp_path / "sceneA.mp4",
        scene_a_len=2.5,
        audio_start=0.9,
    )
    joined = " ".join(cmd)
    assert cmd[:3] == ["ffmpeg", "-nostdin", "-y"]
    assert "-ss 0.900" in joined                       # пред-seek аудио исходника к T-a
    assert "[1:v]" in joined and "scale=1080:1920:force_original_aspect_ratio=increase" in joined
    assert "crop=1080:1920" in joined
    assert "[0:a]atrim=start=0:duration=2.500" in joined  # звук исходника, окно длиной a
    assert "[1:a]" not in joined                        # звук лица выключен
    assert "hflip" not in joined


def test_punch_scene_seeks_to_drop_and_keeps_audio(tmp_path: Path) -> None:
    cmd = build_punch_scene_command(
        source_path=tmp_path / "src.mp4",
        output_path=tmp_path / "sceneB.mp4",
        drop_at=3.4,
    )
    joined = " ".join(cmd)
    assert "-ss 3.400" in joined
    assert "[0:a]" in joined                            # звук панча из исходника
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in joined
    assert "hflip" not in joined
