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


from src.meme_video import Box, text_for_overlay
from src.meme_machine import (
    VariantLook, MemeCaptionCfg, variant_look, build_caption_overlay_command,
)


def _cfg(tmp_path: Path) -> MemeCaptionCfg:
    return MemeCaptionCfg(
        font_regular=tmp_path / "reg.ttf",
        font_bold=tmp_path / "bold.ttf",
        palette=["white", "#FFD400", "#00E0FF"],
        bold_cycle=[True, False],
        top_box=Box(x=40, y=120, w=1000, h=360),
        bottom_box=Box(x=40, y=1440, w=1000, h=360),
        font_size=72,
        max_chars_per_line=20,
    )


def test_variant_look_cycles_color_and_weight(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    assert variant_look(0, cfg) == VariantLook(color="white", font_file=tmp_path / "bold.ttf")
    assert variant_look(1, cfg) == VariantLook(color="#FFD400", font_file=tmp_path / "reg.ttf")
    assert variant_look(3, cfg).color == "white"        # 3 % 3 == 0
    assert variant_look(3, cfg).font_file == (tmp_path / "reg.ttf")  # 3 % 2 == 1 -> regular


def test_overlay_draws_caption_a_and_skips_kept_punch(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    plan = MemePlan(source=Path("s.mp4"), drop_at=3.4, beats=[
        Beat("setup", "face", "top", "own"),
        Beat("punch", "source", "top", "keep"),
    ])
    pair = CaptionPair(a="сетап", b=None)
    cmd = build_caption_overlay_command(
        input_path=tmp_path / "in.mp4", output_path=tmp_path / "out.mp4",
        total_dur=6.0, scene_a_len=3.4, plan=plan,
        pair=pair,
        look=VariantLook(color="white", font_file=tmp_path / "bold.ttf"), cfg=cfg,
        work_dir=tmp_path,
    )
    joined = " ".join(cmd)
    assert "drawtext=" in joined
    assert "fontcolor=white" in joined
    assert "enable='between(t,0.000,3.400)'" in joined  # подпись A на сцене A
    assert joined.count("drawtext=") == 1               # панч keep -> подпись B не рисуется
    assert "hflip" not in joined
    # регрессия на .notdef-баг: текст читается из файла, а не инлайном
    assert "textfile=" in joined
    assert "text='" not in joined                       # нет инлайн-текста
    assert "\n" not in joined                           # сырой перевод строки не течёт в argv
    cap_a = tmp_path / "cap_a_0.txt"                     # одна строка -> один per-line файл
    assert cap_a.exists()
    assert cap_a.read_text(encoding="utf-8") == text_for_overlay(
        pair.a, max_chars_per_line=cfg.max_chars_per_line
    )


def test_overlay_draws_caption_b_with_cover_when_overlay_mode(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    plan = MemePlan(source=Path("s.mp4"), drop_at=3.4, beats=[
        Beat("setup", "face", "top", "own"),
        Beat("punch", "source", "top", "overlay", cover=Box(x=0, y=1400, w=1080, h=300)),
    ])
    pair = CaptionPair(a="сетап", b="панч")
    cmd = build_caption_overlay_command(
        input_path=tmp_path / "in.mp4", output_path=tmp_path / "out.mp4",
        total_dur=6.0, scene_a_len=3.4, plan=plan,
        pair=pair,
        look=VariantLook(color="#FFD400", font_file=tmp_path / "bold.ttf"), cfg=cfg,
        work_dir=tmp_path,
    )
    joined = " ".join(cmd)
    assert joined.count("drawtext=") == 2               # A + B
    assert "drawbox=x=0:y=1400:w=1080:h=300" in joined  # плашка-крышка под B
    assert "enable='between(t,3.400,6.000)'" in joined  # подпись B на сцене B
    assert "fontcolor=#FFD400" in joined
    # регрессия на .notdef-баг: обе подписи читаются из файлов
    assert "textfile=" in joined
    assert "text='" not in joined                       # нет инлайн-текста
    assert "\n" not in joined                           # сырой перевод строки не течёт в argv
    cap_a = tmp_path / "cap_a_0.txt"                     # одна строка -> один per-line файл
    cap_b = tmp_path / "cap_b_0.txt"
    assert cap_a.exists() and cap_b.exists()
    assert cap_a.read_text(encoding="utf-8") == text_for_overlay(
        pair.a, max_chars_per_line=cfg.max_chars_per_line
    )
    assert cap_b.read_text(encoding="utf-8") == text_for_overlay(
        pair.b, max_chars_per_line=cfg.max_chars_per_line
    )


def test_overlay_multiline_caption_splits_into_per_line_drawtexts(tmp_path: Path) -> None:
    # Регрессия на .notdef-баг: этот билд ffmpeg рисует сырой перевод строки (LF)
    # внутри одного drawtext как квадрат «нет глифа». Лечим тем, что каждая
    # обёрнутая строка становится отдельным одностроковым drawtext — ни в argv,
    # ни в textfile нет ни одного \n.
    cfg = _cfg(tmp_path)
    long_caption = "спросил тимлида про мой пиар реквест"
    wrapped = text_for_overlay(long_caption, max_chars_per_line=cfg.max_chars_per_line)
    assert wrapped.count("\n") == 1                      # выбранная строка рвётся ровно на 2
    plan = MemePlan(source=Path("s.mp4"), drop_at=3.4, beats=[
        Beat("setup", "face", "top", "own"),
        Beat("punch", "source", "top", "keep"),          # панч keep -> только подпись A
    ])
    pair = CaptionPair(a=long_caption, b=None)
    cmd = build_caption_overlay_command(
        input_path=tmp_path / "in.mp4", output_path=tmp_path / "out.mp4",
        total_dur=6.0, scene_a_len=3.4, plan=plan,
        pair=pair,
        look=VariantLook(color="white", font_file=tmp_path / "bold.ttf"), cfg=cfg,
        work_dir=tmp_path,
    )
    joined = " ".join(cmd)
    assert joined.count("drawtext=") == 2                # 2 строки -> 2 отдельных drawtext
    assert "\n" not in joined                            # сырой перевод строки не течёт в argv
    cap_0 = tmp_path / "cap_a_0.txt"
    cap_1 = tmp_path / "cap_a_1.txt"
    assert cap_0.exists() and cap_1.exists()
    assert cap_0.read_text(encoding="utf-8") == wrapped.split("\n")[0]
    assert cap_1.read_text(encoding="utf-8") == wrapped.split("\n")[1]
    assert "\n" not in cap_0.read_text(encoding="utf-8")  # каждый файл одностроковый
    assert "\n" not in cap_1.read_text(encoding="utf-8")


from src.meme_machine import plan_variants, MemeVariant


def test_plan_variants_names_and_assigns_faces(tmp_path: Path) -> None:
    faces = [tmp_path / f"{i}.mp4" for i in range(1, 3)]  # 2 лица
    for f in faces:
        f.write_text("x")
    pairs = [
        CaptionPair(a="Дал Claude задачу собрать субагентов", b="что получаю"),
        CaptionPair(a="Второй сетап про лимиты", b=None),
        CaptionPair(a="Третий сетап", b=None),
    ]
    variants = plan_variants(pairs=pairs, faces=faces, seed=7, publish_dir=tmp_path / "pub")
    assert [v.index for v in variants] == [1, 2, 3]
    assert variants[0].final_path.name.startswith("01_")
    assert variants[2].final_path.name.startswith("03_")
    assert all(v.face_path in faces for v in variants)   # лица из пула
    # повторяемость назначения
    again = plan_variants(pairs=pairs, faces=faces, seed=7, publish_dir=tmp_path / "pub")
    assert [v.face_path for v in variants] == [v.face_path for v in again]


from src.schemas import load_config
from src.meme_machine import caption_cfg_from_config, MemeCaptionCfg


def test_config_yaml_exposes_meme_caption_defaults() -> None:
    cfg = load_config()               # грузит config.yaml как есть
    mc = caption_cfg_from_config(cfg)
    assert isinstance(mc, MemeCaptionCfg)
    assert len(mc.palette) >= 3       # ≥3 цвета для дедупа
    assert mc.bold_cycle              # непусто
    assert mc.font_regular.suffix == ".ttf"
