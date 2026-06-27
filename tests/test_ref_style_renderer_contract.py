from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.ref_style_director import RefStyleEditPlan, RefStyleEditSegment
from src.schemas import Transcript, Word
from pipelines import render_ref_style_directed as renderer


def _ass_seconds(value: str) -> float:
    hours, minutes, rest = value.split(":")
    seconds, centiseconds = rest.split(".")
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(centiseconds) / 100
    )


def _segment(
    start: float,
    end: float,
    format_id: str,
    role: str,
    text: str,
) -> RefStyleEditSegment:
    return RefStyleEditSegment(
        start=start,
        end=end,
        text=text,
        semantic_role=role,
        format_id=format_id,
        subtitle_mode="test",
        product_demo=format_id in {"format_4_blue_demo", "format_5_lower_demo_cta"},
        reason="test",
        confidence=0.9,
    )


def test_hook_zoom_chain_uses_visible_center_locked_zoom_out() -> None:
    chain = renderer.hook_zoom_chain()

    assert "zoompan=z='1+(0.28000*(1-(" in chain
    assert "x='iw/2-(iw/zoom/2)'" in chain
    assert "y='ih/2-(ih/zoom/2)'" in chain


def test_format_4_ass_uses_editorial_yellow_white_instead_of_red(tmp_path: Path) -> None:
    transcript = Transcript(
        words=[
            Word(word="загрузить", start=0.0, end=0.5),
            Word(word="резюме", start=0.6, end=1.0),
            Word(word="полный", start=1.1, end=1.5),
            Word(word="отчет", start=1.6, end=2.0),
        ],
        full_text="загрузить резюме полный отчет",
        duration=2.2,
    )
    plan = RefStyleEditPlan(
        source="new!.MOV",
        duration=2.2,
        segments=[
            _segment(0.0, 2.2, "format_4_blue_demo", "product_demo", "загрузить резюме полный отчет")
        ],
    )
    ass_path = tmp_path / "subs.ass"

    renderer.write_ass(ass_path, transcript, plan)

    ass = ass_path.read_text(encoding="utf-8")
    assert "PosterRed" not in ass
    assert "&H000000F6" not in ass
    # Accent word is yellow, the rest stay white (no red).
    assert "&H0000EAFF" in ass  # yellow accent
    assert "&H00FFFFFF" in ass  # white non-accent


def test_hook_words_do_not_overlap_on_screen(tmp_path: Path) -> None:
    transcript = Transcript(
        words=[
            Word(word="рынок", start=0.00, end=0.20),
            Word(word="найма", start=0.20, end=0.48),
            Word(word="в", start=0.48, end=0.58),
        ],
        full_text="рынок найма в",
        duration=0.8,
    )
    plan = RefStyleEditPlan(
        source="new!.MOV",
        duration=0.8,
        segments=[_segment(0.0, 0.8, "format_1_hook_metal", "hook_problem", "рынок найма в")],
    )
    ass_path = tmp_path / "subs.ass"

    renderer.write_ass(ass_path, transcript, plan)

    hook_main_events: list[tuple[float, float, str]] = []
    for line in ass_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue: 4,"):
            continue
        parts = line.split(",", 9)
        hook_main_events.append((_ass_seconds(parts[1]), _ass_seconds(parts[2]), parts[9]))

    assert len(hook_main_events) == 3
    for current, following in zip(hook_main_events, hook_main_events[1:]):
        assert current[1] <= following[0]


def test_pause_tight_chunks_trim_long_gaps_to_short_residual_pause() -> None:
    transcript = Transcript(
        words=[
            Word(word="первое", start=0.10, end=0.30),
            Word(word="второе", start=1.10, end=1.40),
            Word(word="третье", start=1.52, end=1.72),
            Word(word="финал", start=2.40, end=2.70),
        ],
        full_text="первое второе третье финал",
        duration=3.0,
    )

    chunks = renderer.pause_tight_chunks(
        transcript,
        max_gap_sec=0.20,
        retain_gap_sec=0.12,
        edge_pad_sec=0.06,
    )

    assert chunks == [
        {"start": 0.04, "end": 0.36},
        {"start": 1.04, "end": 1.78},
        {"start": 2.34, "end": 2.88},
    ]

    tightened = renderer.tighten_transcript_to_chunks(transcript, chunks)
    assert [round(word.start, 2) for word in tightened.words] == [0.06, 0.38, 0.80, 1.12]
    assert round(tightened.duration, 2) == 1.60


def test_default_pause_tight_chunks_keep_word_tails_and_modest_phrase_gaps() -> None:
    transcript = Transcript(
        words=[
            Word(word="первое", start=0.10, end=0.30),
            Word(word="второе", start=0.58, end=0.80),
            Word(word="третье", start=2.10, end=2.40),
        ],
        full_text="первое второе третье",
        duration=2.9,
    )

    chunks = renderer.pause_tight_chunks(transcript)

    assert len(chunks) == 2
    assert chunks[0]["start"] == 0.0
    assert chunks[0]["end"] >= 1.0
    assert chunks[1]["start"] <= 1.80
    # The cut keeps a generous tail after the last word of the first phrase so
    # word endings are never swallowed at the join.
    assert chunks[0]["end"] - 0.80 >= 0.40


def test_default_pause_tight_chunks_keep_natural_phrase_pause_after_cut() -> None:
    transcript = Transcript(
        words=[
            Word(word="фраза", start=0.10, end=0.35),
            Word(word="следующая", start=1.20, end=1.55),
        ],
        full_text="фраза следующая",
        duration=2.0,
    )

    chunks = renderer.pause_tight_chunks(transcript)

    retained_gap = (chunks[0]["end"] - 0.35) + (1.20 - chunks[1]["start"])
    assert retained_gap >= 0.50
    assert chunks[0]["end"] - 0.35 >= 0.30


def test_pause_tight_chunks_merge_overlapping_padded_ranges() -> None:
    transcript = Transcript(
        words=[
            Word(word="первое", start=0.10, end=0.35),
            Word(word="второе", start=0.86, end=1.15),
        ],
        full_text="первое второе",
        duration=1.6,
    )

    chunks = renderer.pause_tight_chunks(transcript)

    assert chunks == [{"start": 0.0, "end": 1.57}]


def test_ass_display_fixes_common_clean_transcript_errors(tmp_path: Path) -> None:
    transcript = Transcript(
        words=[
            Word(word="провера.", start=0.0, end=0.5),
            Word(word="совреть", start=0.6, end=1.0),
        ],
        full_text="провера. совреть",
        duration=1.2,
    )
    plan = RefStyleEditPlan(
        source="new!.MOV",
        duration=1.2,
        segments=[
            _segment(0.0, 1.2, "format_5_lower_demo_cta", "cta", "провера. совреть")
        ],
    )
    ass_path = tmp_path / "subs.ass"

    renderer.write_ass(ass_path, transcript, plan)

    ass = ass_path.read_text(encoding="utf-8")
    assert "ПРОВЕРЯЙ" in ass
    assert "СВОЁ" in ass
    assert "ПРОВЕРА" not in ass
    assert "СОВРЕТЬ" not in ass


def test_scene_5_blur_is_limited_to_demo_area() -> None:
    plan = RefStyleEditPlan(
        source="new!.MOV",
        duration=4.0,
        segments=[
            _segment(0.0, 2.0, "format_1_hook_metal", "hook_problem", "hook"),
            _segment(2.0, 4.0, "format_5_lower_demo_cta", "cta", "cta"),
        ],
    )

    with patch.object(renderer, "run") as run:
        renderer.render_base(
            Path("base.mp4"),
            source=Path("clean.mp4"),
            product=Path("product.mov"),
            music=Path("music.mp3"),
            sfx_swish=Path("swoosh.mp3"),
            duration=4.0,
            plan=plan,
        )

    cmd = run.call_args.args[0]
    filter_complex = cmd[cmd.index("-filter_complex") + 1]
    assert "boxblur=18" not in filter_complex
    assert "[soft]" not in filter_complex
    # Lower-demo card is only lightly blurred (rounded + mostly opaque).
    assert "boxblur=0.4:1" in filter_complex


def test_scene4_framed_face_is_a_plain_closeup_without_card_or_backdrop() -> None:
    # Scene 4 is now "говорящая голова крупно": a full-frame close-up head, no photo
    # card and no yellow/video backdrop. The old framed-card composition is retired.
    plan = RefStyleEditPlan(
        source="new!.MOV",
        duration=4.0,
        segments=[
            _segment(0.0, 4.0, "format_2_framed_face", "workflow_explain", "yellow"),
        ],
    )

    with patch.object(renderer, "run") as run:
        renderer.render_base(
            Path("base.mp4"),
            source=Path("clean.mp4"),
            product=Path("product.mov"),
            music=Path("music.mp3"),
            sfx_swish=Path("swoosh.mp3"),
            duration=4.0,
            plan=plan,
        )

    cmd = run.call_args.args[0]
    filter_complex = cmd[cmd.index("-filter_complex") + 1]
    # No photo card, no yellow grid backdrop, no frame overlay.
    assert "crop=872:1040" not in filter_complex
    assert "[frame2]" not in filter_complex
    assert "0xdfc52e" not in filter_complex  # the old yellow grid colour
    assert "[talk][yellow]" not in filter_complex
    # The composed head flows straight into the (unused-here) blue cover layer.
    assert "[talk][blue]overlay=0:0" in filter_complex
    # The head is shown close — the close-up push-in is enabled across the beat.
    assert "[talkMed][talkClose]overlay=0:0:enable='between(t,0.00,4.00)'" in filter_complex


def test_blue_demo_card_fits_product_clip_whole_without_cropping() -> None:
    plan = RefStyleEditPlan(
        source="new!.MOV",
        duration=3.0,
        segments=[
            _segment(0.0, 3.0, "format_4_blue_demo", "product_demo", "ats"),
        ],
    )

    with patch.object(renderer, "run") as run:
        renderer.render_base(
            Path("base.mp4"),
            source=Path("clean.mp4"),
            product=Path("product.mov"),
            music=Path("music.mp3"),
            sfx_swish=Path("swoosh.mp3"),
            duration=3.0,
            plan=plan,
        )

    cmd = run.call_args.args[0]
    filter_complex = cmd[cmd.index("-filter_complex") + 1]
    # The product clip is shown WHOLE (fit to its own aspect), never centre-cropped,
    # so no UI is cut off. A 2:3 source fits the 1000x1500 box exactly.
    assert "force_original_aspect_ratio=decrease" in filter_complex
    assert "crop=1000:1500:(iw-1000)/2:(ih-1500)/2" not in filter_complex


def test_product_demo_sequence_uses_multiple_inputs_for_demo_segments() -> None:
    plan = RefStyleEditPlan(
        source="new!.MOV",
        duration=14.0,
        segments=[
            _segment(0.0, 3.0, "format_4_blue_demo", "product_demo", "ats"),
            _segment(3.4, 6.0, "format_2_framed_face", "workflow_explain", "yellow"),
            _segment(6.4, 9.0, "format_4_blue_demo", "product_demo", "match"),
            _segment(9.4, 12.0, "format_4_blue_demo", "product_demo", "letter"),
            _segment(12.3, 14.0, "format_5_lower_demo_cta", "cta", "cta"),
        ],
    )

    with patch.object(renderer, "run") as run:
        renderer.render_base(
            Path("base.mp4"),
            source=Path("clean.mp4"),
            product=[
                Path("sa_1_demo.mp4"),
                Path("sa_2_demo.mp4"),
                Path("sa_3_demo.mp4"),
                Path("sa_4_demo.mp4"),
            ],
            music=Path("music.mp3"),
            sfx_swish=Path("swoosh.mp3"),
            duration=14.0,
            plan=plan,
        )

    cmd = run.call_args.args[0]
    assert cmd.count("-i") == 7
    for asset in ["sa_1_demo.mp4", "sa_2_demo.mp4", "sa_3_demo.mp4", "sa_4_demo.mp4"]:
        assert asset in [str(part) for part in cmd]
    filter_complex = cmd[cmd.index("-filter_complex") + 1]
    assert "[1:v]trim=start=0:duration=3.000" in filter_complex
    assert "[2:v]trim=start=0:duration=2.600" in filter_complex
    assert "[3:v]trim=start=0:duration=2.600" in filter_complex
    assert "[4:v]trim=start=0:duration=1.700" in filter_complex
    assert "[5:a]atrim=0:14.000" in filter_complex
    assert "[6:a]asplit=4" in filter_complex


def test_assign_demo_products_matches_clip_to_spoken_words() -> None:
    segs = [
        _segment(0.0, 3.0, "format_4_blue_demo", "product_demo",
                 "он анализирует резюме по 8 критериям включая АТС"),
        _segment(3.0, 6.0, "format_4_blue_demo", "product_demo",
                 "показывает соответствие и что нужно подтянуть"),
        _segment(6.0, 9.0, "format_5_lower_demo_cta", "cta",
                 "ссылка в шапке профиля"),
    ]
    tags = [
        ["резюме", "адаптир"],          # clip 0: resume
        ["соответствие", "подтянуть"],  # clip 1: match score
        ["критери", "атс"],             # clip 2: criteria analysis
        ["письмо"],                      # clip 3: cover letter
    ]
    assignment = renderer.assign_demo_products(segs, tags)
    assert assignment[0] == 2          # "критериям/АТС" -> criteria clip
    assert assignment[1] == 1          # "соответствие" -> match clip
    assert 0 <= assignment[2] < 4      # CTA has no demo keyword -> valid fallback


def test_assign_demo_products_round_robin_without_tags() -> None:
    segs = [
        _segment(i, i + 1, "format_4_blue_demo", "product_demo", "x")
        for i in range(3)
    ]
    assert renderer.assign_demo_products(segs, [[], [], []]) == [0, 1, 2]


def test_visual_format_enable_spans_until_next_scene_start() -> None:
    plan = RefStyleEditPlan(
        source="new!.MOV",
        duration=2.2,
        segments=[
            _segment(0.0, 1.0, "format_2_framed_face", "workflow_explain", "yellow"),
            _segment(1.2, 2.0, "format_4_blue_demo", "product_demo", "blue"),
        ],
    )

    assert renderer.enable_expr(plan, "format_2_framed_face") == "between(t,0.00,1.20)"


def test_directed_duration_keeps_tight_video_tail_after_last_word() -> None:
    transcript = Transcript(
        words=[Word(word="финал", start=2.40, end=2.70)],
        full_text="финал",
        duration=3.0,
    )
    plan = RefStyleEditPlan(
        source="new!.MOV",
        duration=3.0,
        segments=[_segment(0.0, 2.72, "format_5_lower_demo_cta", "cta", "финал")],
    )

    assert renderer.directed_duration(transcript, plan) == 3.0


def test_prepare_clean_prelayer_reuses_existing_talking_head_renderer(tmp_path: Path) -> None:
    source = tmp_path / "new!.MOV"
    source.write_bytes(b"video")
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    (clean_dir / "edit_decisions.json").write_text(
        """
{
  "chunks": [
    {
      "index": 0,
      "source_index": 0,
      "source": "new!.MOV",
      "start": 0.0,
      "end": 0.5,
      "duration": 0.5,
      "plan": "medium",
      "transition": "micro_push"
    }
  ]
}
""",
        encoding="utf-8",
    )
    transcript = Transcript(
        words=[Word(word="тест", start=0.0, end=0.4)],
        full_text="тест",
        duration=0.5,
    )

    with (
        patch.object(renderer, "run") as run,
        patch.object(renderer, "load_transcript", return_value=transcript) as load_transcript,
    ):
        clean_video, clean_transcript, transcript_path = renderer.prepare_clean_prelayer(
            source,
            clean_dir=clean_dir,
            force=True,
        )

    cmd = run.call_args.args[0]
    assert "render_talking_head_dynamic_clean.py" in " ".join(str(part) for part in cmd)
    assert "--retake-mode" in cmd
    assert "off" in cmd
    assert clean_video == clean_dir / "final_clean.mp4"
    assert transcript_path == clean_dir / "transcript.json"
    assert clean_transcript == transcript
    load_transcript.assert_called_once_with(source, clean_dir / "source.transcript.json")


def test_parse_args_does_not_tighten_pauses_by_default() -> None:
    with patch("sys.argv", ["render_ref_style_directed.py"]):
        args = renderer.parse_args()

    assert args.tighten_pauses is False


def test_transcript_protected_chunks_restore_words_inside_detected_silence() -> None:
    chunks = [
        {
            "index": 0,
            "source_index": 0,
            "source": "new!.MOV",
            "start": 1.1,
            "end": 4.0,
            "duration": 2.9,
            "plan": "medium",
            "transition": "micro_push",
        }
    ]
    transcript = Transcript(
        words=[
            Word(word="Я", start=0.58, end=1.32),
            Word(word="тест", start=2.0, end=2.3),
        ],
        full_text="Я тест",
        duration=4.0,
    )

    protected = renderer.transcript_protected_chunks(chunks, transcript, source="new!.MOV")

    assert protected[0]["start"] <= 0.46
    assert protected[0]["end"] == 4.0


def test_restore_missing_terminal_words_keeps_zero_duration_tail_word() -> None:
    source = Transcript(
        words=[
            Word(word="Продолжение", start=30.02, end=30.32),
            Word(word="следует.", start=30.32, end=30.32),
        ],
        full_text="Продолжение следует.",
        duration=30.32,
    )
    clean = Transcript(
        words=[Word(word="Продолжение", start=28.0, end=28.2)],
        full_text="Продолжение",
        duration=28.34,
    )

    restored = renderer.restore_missing_terminal_words(source, clean)

    assert restored.full_text == "Продолжение следует."
    assert restored.words[-1].word == "следует."
    assert restored.words[-1].end <= restored.duration
