from __future__ import annotations

from pathlib import Path

import pytest

from src.meme_video import (
    BILLION_PROFILE,
    Box,
    LayoutProfile,
    TextSpec,
    build_concat_command,
    build_dedup_command,
    build_jobs,
    build_opening_command,
    build_outro_command,
    build_qa_sheet_command,
    build_text_overlay_command,
    collect_broll_files,
    load_texts_file,
    punch_filename_stem,
    render_text_lines,
    resolve_template_path,
    safe_filename_stem,
    text_for_overlay,
)


def test_collect_broll_files_ignores_non_video_and_sorts_numeric(tmp_path: Path) -> None:
    for name in ["7.mp4", "4.mp4", ".DS_Store", "10.mov", "2.mov", "alpha.mp4"]:
        (tmp_path / name).write_text("x")

    files = collect_broll_files(tmp_path)

    assert [path.name for path in files] == ["2.mov", "4.mp4", "7.mp4", "10.mov", "alpha.mp4"]


def test_build_jobs_uses_one_video_per_text_and_cycles_broll(tmp_path: Path) -> None:
    brolls = [tmp_path / f"{i}.mp4" for i in range(1, 4)]
    texts = [f"Фраза номер {i}" for i in range(1, 6)]

    jobs = build_jobs(texts=texts, broll_files=brolls, publish_dir=tmp_path / "pub")

    assert len(jobs) == 5
    assert [job.broll_path.name for job in jobs] == ["1.mp4", "2.mp4", "3.mp4", "1.mp4", "2.mp4"]
    assert jobs[0].final_path.name.startswith("01_фраза_номер_1")
    assert jobs[4].final_path.name.startswith("05_фраза_номер_5")


def test_build_jobs_rejects_missing_broll(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No b-roll"):
        build_jobs(texts=["Есть текст"], broll_files=[], publish_dir=tmp_path)


def test_safe_filename_stem_is_short_russian_readable() -> None:
    text = "Ты выиграл миллиард но больше не сможешь нюхать как коллега греет рыбу в офисе"

    assert safe_filename_stem(text, max_words=8) == "ты_выиграл_миллиард_но_больше_не_сможешь_нюхать"


@pytest.mark.parametrize(
    ("text", "stem"),
    [
        (
            "Ты выиграл миллиард но больше не сможешь нюхать как коллега греет рыбу в офисе",
            "коллега_греет_рыбу_в_офисе",
        ),
        (
            "Ты выиграл миллиард но больше не сможешь стоять в очереди на маршруту утром на работу",
            "очереди_на_маршруту_утром",
        ),
        (
            "Ты выиграл миллиард но больше не сможешь ловить СДВГ от 5 параллельных чатов со вторым мозгом на Обсидиан",
            "сдвг_от_5_параллельных_чатов",
        ),
        (
            "Ты выиграл миллиард но больше не можешь слышать коллеги, подключаемся в зум, ссылочка на почте, не опаздываем",
            "коллеги_подключаемся_в_зум",
        ),
        (
            "Ты выиграл миллиард но больше не сможешь слышать как начальник мотивирует тебя фразой «Чебупель ты мой, можешь же когда захочешь»",
            "чебупель_ты_мой_можешь_же",
        ),
    ],
)
def test_punch_filename_stem_uses_unique_part_of_meme_text(text: str, stem: str) -> None:
    assert punch_filename_stem(text, max_words=5) == stem


def test_build_jobs_names_files_from_unique_punch_part(tmp_path: Path) -> None:
    jobs = build_jobs(
        texts=["Ты выиграл миллиард но больше не сможешь нюхать как коллега греет рыбу в офисе"],
        broll_files=[tmp_path / "1.mp4"],
        publish_dir=tmp_path / "pub",
    )

    assert jobs[0].final_path.name == "01_коллега_греет_рыбу_в_офисе.mp4"


def test_build_jobs_names_quoted_punch_without_dangling_tail(tmp_path: Path) -> None:
    jobs = build_jobs(
        texts=[
            "Ты выиграл миллиард но больше не сможешь слышать как начальник мотивирует тебя фразой «Чебупель ты мой, можешь же когда захочешь»"
        ],
        broll_files=[tmp_path / "1.mp4"],
        publish_dir=tmp_path / "pub",
    )

    assert jobs[0].final_path.name == "01_чебупель_ты_мой_можешь_же.mp4"


def test_render_text_lines_wraps_long_lines_for_vertical_video() -> None:
    spec = TextSpec(
        text="Ты выиграл миллиард но больше не сможешь ловить СДВГ от 5 параллельных чатов",
        max_chars_per_line=22,
    )

    lines = render_text_lines(spec)

    assert len(lines) >= 3
    assert all(len(line) <= 24 for line in lines)


def test_load_texts_file_accepts_numbered_chat_list(tmp_path: Path) -> None:
    path = tmp_path / "texts.txt"
    path.write_text(
        "\n".join(
            [
                "1. Первый текст",
                "2) Второй текст",
                "",
                "- третий текст",
            ]
        ),
        encoding="utf-8",
    )

    assert load_texts_file(path) == ["Первый текст", "Второй текст", "третий текст"]


def test_resolve_template_path_uses_draft_root_for_relative_names(tmp_path: Path) -> None:
    draft_root = tmp_path / "drafts"
    draft_root.mkdir()
    template = draft_root / "исходник.mp4"
    template.write_text("x")

    assert resolve_template_path(Path("исходник.mp4"), draft_root=draft_root) == template


def test_billion_profile_matches_manual_example_timing() -> None:
    assert BILLION_PROFILE.opening_duration == 3.5
    assert BILLION_PROFILE.outro_start == 4.933333
    assert BILLION_PROFILE.outro_duration == 5.766667
    assert BILLION_PROFILE.opening_text_box.y == 210


def test_billion_profile_keeps_long_opening_text_to_five_lines() -> None:
    text = "Ты выиграл миллиард но больше не сможешь ловить СДВГ от 5 параллельных чатов со вторым мозгом на Обсидиан"

    overlay = text_for_overlay(text, max_chars_per_line=BILLION_PROFILE.opening_max_chars_per_line)

    assert len(overlay.splitlines()) <= 5


def test_dedup_command_contains_required_filters_without_mirroring(tmp_path: Path) -> None:
    command = build_dedup_command(tmp_path / "assembled.mp4", tmp_path / "final.mp4")
    joined = " ".join(command)

    assert "setpts=" not in joined
    assert "lenscorrection=k1=0.04:k2=0.02" in joined
    assert "crop=iw*0.95:ih*0.95:(iw*0.03):(ih*0.028)" in joined
    assert "noise=c0s=4:c0f=t+u" in joined
    assert "vignette=PI/6" in joined
    assert "-map_metadata -1" in joined
    assert "hflip" not in joined
    assert "atempo" not in joined
    assert "asetrate" not in joined


def test_layout_profile_scales_boxes_to_output_resolution() -> None:
    profile = LayoutProfile(
        name="test",
        opening_duration=3.4,
        outro_start=3.4,
        opening_text_box=Box(x=60, y=90, w=960, h=280),
        outro_text_box=Box(x=80, y=1020, w=920, h=170),
    )

    assert profile.opening_text_box.drawbox_filter("black") == "drawbox=x=60:y=90:w=960:h=280:color=black@1.0:t=fill"


def test_opening_command_uses_template_audio_and_no_backdrop(tmp_path: Path) -> None:
    profile = LayoutProfile(
        name="test",
        opening_duration=3.4,
        outro_start=3.4,
        opening_text_box=Box(x=60, y=80, w=960, h=300),
        outro_text_box=Box(x=80, y=1020, w=920, h=180),
    )

    command = build_opening_command(
        broll_path=tmp_path / "1.mp4",
        template_path=tmp_path / "template.mp4",
        output_path=tmp_path / "opening.mp4",
        profile=profile,
        text_file=tmp_path / "opening.txt",
        font_file=tmp_path / "font.ttf",
    )
    joined = " ".join(command)

    assert "-stream_loop -1" in joined
    assert "-t 3.400" in joined
    assert "drawbox=" not in joined
    assert "[1:a]atrim=start=0:duration=3.400" in joined
    assert "[0:a]" not in joined
    assert "drawtext=fontfile=" not in joined
    assert "textfile=" not in joined
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in joined
    assert "crop=1080:1920" in joined
    assert "hflip" not in joined


def test_outro_command_keeps_background_clean_for_final_text_pass(tmp_path: Path) -> None:
    command = build_outro_command(
        template_path=tmp_path / "template.mp4",
        output_path=tmp_path / "outro.mp4",
        profile=BILLION_PROFILE,
        text_file=tmp_path / "outro.txt",
        font_file=tmp_path / "font.ttf",
    )
    joined = " ".join(command)

    assert "-ss 4.933" in joined
    assert BILLION_PROFILE.outro_text_box.x == 0
    assert BILLION_PROFILE.outro_text_box.w == 1080
    assert BILLION_PROFILE.outro_text_box.y + BILLION_PROFILE.outro_text_box.h <= 660
    assert "drawbox=" not in joined
    assert "drawtext=fontfile=" not in joined
    assert "textfile=" not in joined


def test_text_overlay_command_draws_crisp_text_after_dedup(tmp_path: Path) -> None:
    command = build_text_overlay_command(
        input_path=tmp_path / "dedup_background.mp4",
        output_path=tmp_path / "final.mp4",
        profile=BILLION_PROFILE,
        opening_text_file=tmp_path / "opening.txt",
        outro_text_file=tmp_path / "outro.txt",
        font_file=tmp_path / "font.ttf",
    )
    joined = " ".join(command)

    assert str(tmp_path / "dedup_background.mp4") in joined
    assert "lenscorrection" not in joined
    assert "noise=c0s" not in joined
    assert "drawtext=fontfile=" in joined
    assert "textfile=" in joined
    assert "borderw=2" in joined
    assert "bordercolor=black@0.45" in joined
    assert "shadowx=2" in joined
    assert "shadowy=2" in joined
    assert "shadowcolor=black@0.35" in joined
    assert "setpts=PTS-STARTPTS" in joined
    assert "-bf 0" in joined
    assert "enable='between(t,0.000,3.500)'" in joined
    assert "drawbox=x=0:y=330:w=1080:h=320:color=black@1.0:t=fill:enable='between(t,3.500,9.267)'" in joined
    assert "enable='between(t,3.500,9.267)'" in joined


def test_concat_and_qa_commands_are_output_local(tmp_path: Path) -> None:
    concat_list = tmp_path / "concat.txt"
    command = build_concat_command(concat_list, tmp_path / "assembled.mp4")
    qa = build_qa_sheet_command(tmp_path / "final.mp4", tmp_path / "qa.jpg")

    assert command[:6] == ["ffmpeg", "-nostdin", "-y", "-f", "concat", "-safe"]
    assert str(concat_list) in command
    assert "fps=1" in " ".join(qa)
    assert "tile=3x3" in " ".join(qa)
