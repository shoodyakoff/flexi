from __future__ import annotations

from src.ref_style_director import (
    RefStyleEditPlan,
    RefStyleEditSegment,
    build_edit_plan,
    validate_edit_plan,
)
from src.schemas import Transcript, Word


def _word(text: str, start: float, end: float) -> Word:
    return Word(word=text, start=start, end=end)


def _semantic_transcript() -> Transcript:
    words = [
        _word("Я", 0.0, 0.1),
        _word("устал", 0.2, 0.7),
        _word("адаптировать", 0.8, 1.4),
        _word("резюме", 1.5, 1.9),
        _word("поэтому", 2.7, 3.0),
        _word("сделал", 3.3, 3.8),
        _word("сервис", 4.2, 4.9),
        _word("тебе", 5.8, 6.1),
        _word("нужно", 6.2, 6.5),
        _word("зайти", 6.6, 7.0),
        _word("загрузить", 7.1, 7.7),
        _word("вакансию", 7.8, 8.4),
        _word("интервью", 9.2, 9.8),
        _word("адаптируем", 10.0, 10.8),
        _word("письмо", 11.0, 11.8),
        _word("ссылка", 12.6, 13.0),
        _word("пользуйся", 13.1, 13.8),
    ]
    return Transcript(words=words, full_text=" ".join(word.word for word in words), duration=14.0)


def test_build_edit_plan_uses_semantic_formats_instead_of_rotation() -> None:
    plan = build_edit_plan(_semantic_transcript(), source="new!.MOV")

    assert [segment.format_id for segment in plan.segments] == [
        "format_1_hook_metal",
        "format_3_turn_badge",
        "format_4_blue_demo",
        "format_2_framed_face",
        "format_5_lower_demo_cta",
    ]
    assert [segment.semantic_role for segment in plan.segments] == [
        "hook_problem",
        "product_intro",
        "product_demo",
        "workflow_explain",
        "cta",
    ]


def test_validate_edit_plan_keeps_known_formats_and_sane_durations() -> None:
    plan = validate_edit_plan(build_edit_plan(_semantic_transcript(), source="new!.MOV"))

    assert all(segment.end > segment.start for segment in plan.segments)
    assert max(segment.end - segment.start for segment in plan.segments) <= 7.2
    assert all(
        segment.end - segment.start >= 2.2
        for segment in plan.segments
        if segment.semantic_role != "cta"
    )
    assert {segment.format_id for segment in plan.segments} <= {
        "format_1_hook_metal",
        "format_2_framed_face",
        "format_3_turn_badge",
        "format_4_blue_demo",
        "format_5_lower_demo_cta",
    }


def test_build_edit_plan_splits_semantic_turn_cues_inside_fluent_sentence() -> None:
    words = [
        _word("Я", 0.0, 0.1),
        _word("устал", 0.2, 0.7),
        _word("адаптировать", 0.8, 1.4),
        _word("резюме", 1.5, 2.2),
        _word("поэтому", 2.5, 2.9),
        _word("сделал", 3.2, 3.7),
        _word("свой", 3.9, 4.3),
        _word("сервис", 4.4, 4.9),
    ]
    transcript = Transcript(
        words=words,
        full_text=" ".join(word.word for word in words),
        duration=5.1,
    )

    plan = build_edit_plan(transcript, source="new!.MOV")

    assert [segment.semantic_role for segment in plan.segments] == [
        "hook_problem",
        "product_intro",
    ]
    assert [segment.format_id for segment in plan.segments] == [
        "format_1_hook_metal",
        "format_3_turn_badge",
    ]


def test_build_edit_plan_treats_ats_product_analysis_as_demo() -> None:
    words = [
        _word("Я", 0.0, 0.1),
        _word("сделал", 0.2, 0.7),
        _word("сервис", 0.8, 1.2),
        _word("Сопровод,", 1.3, 1.8),
        _word("который", 1.9, 2.4),
        _word("анализирует", 2.5, 3.2),
        _word("резюме", 3.3, 3.8),
        _word("по", 3.9, 4.1),
        _word("8", 4.2, 4.4),
        _word("критериям,", 4.5, 5.2),
        _word("включая", 5.3, 5.9),
        _word("АТС.", 6.0, 6.4),
    ]
    transcript = Transcript(
        words=words,
        full_text=" ".join(word.word for word in words),
        duration=6.8,
    )

    plan = build_edit_plan(transcript, source="talking_head.mp4")

    assert len(plan.segments) == 1
    assert plan.segments[0].semantic_role == "product_demo"
    assert plan.segments[0].format_id == "format_4_blue_demo"
    assert plan.segments[0].product_demo is True


def _max_consecutive_run(formats: list[str]) -> int:
    best = run = 0
    previous = None
    for value in formats:
        run = run + 1 if value == previous else 1
        previous = value
        best = max(best, run)
    return best


def test_validate_edit_plan_breaks_long_demo_run_with_head_relief() -> None:
    words = [
        _word("анализирует", 0.0, 0.6),
        _word("резюме", 0.7, 1.2),
        _word("критериям", 1.3, 2.0),
        _word("соответствие", 2.1, 2.7),
        _word("вакансию", 3.4, 4.0),
        _word("отчет", 4.1, 4.6),
        _word("критерию", 4.7, 5.4),
        _word("соответствие", 5.5, 6.1),
        _word("загрузить", 6.8, 7.4),
        _word("вакансии", 7.5, 8.1),
        _word("критерия", 8.2, 8.9),
        _word("анализировать", 9.0, 9.7),
        _word("соответствие", 10.4, 11.0),
        _word("отчет", 11.1, 11.7),
        _word("критериям", 11.8, 12.5),
        _word("вакансию", 12.6, 13.2),
    ]
    transcript = Transcript(
        words=words,
        full_text=" ".join(word.word for word in words),
        duration=13.6,
    )

    plan = build_edit_plan(transcript, source="talking_head.mp4")
    formats = [segment.format_id for segment in plan.segments]

    # The director must produce a long product-demo intent...
    assert formats.count("format_4_blue_demo") >= 2
    # ...but the guardrail must break it so the head returns (no 3-in-a-row).
    assert _max_consecutive_run(formats) <= 2
    assert "format_2_framed_face" in formats
    assert any("run at" in warning for warning in plan.warnings)


def test_validate_edit_plan_absorbs_short_support_section_instead_of_yellow_flash() -> None:
    plan = RefStyleEditPlan(
        source="new!.MOV",
        duration=20.4,
        segments=[
            RefStyleEditSegment(
                start=7.24,
                end=14.04,
                text="Тебе нужно зайти и получить полный отчет",
                semantic_role="product_demo",
                format_id="format_4_blue_demo",
                subtitle_mode="poster_demo",
                product_demo=True,
                reason="product terms",
                confidence=0.86,
            ),
            RefStyleEditSegment(
                start=14.04,
                end=15.44,
                text="по твоему резюме",
                semantic_role="support",
                format_id="format_2_framed_face",
                subtitle_mode="pastry_script",
                reason="supporting tail",
                confidence=0.62,
            ),
            RefStyleEditSegment(
                start=17.76,
                end=20.40,
                text="Дальше ты отвечаешь на интервью",
                semantic_role="workflow_explain",
                format_id="format_2_framed_face",
                subtitle_mode="pastry_script",
                reason="workflow terms",
                confidence=0.78,
            ),
        ],
    )

    validated = validate_edit_plan(plan)

    assert [segment.format_id for segment in validated.segments] == [
        "format_4_blue_demo",
        "format_2_framed_face",
    ]
    assert "по твоему резюме" in validated.segments[0].text
    assert all(
        segment.end - segment.start >= 2.2
        for segment in validated.segments
        if segment.semantic_role != "cta"
    )


def test_validate_edit_plan_merges_consecutive_cta_beats() -> None:
    plan = RefStyleEditPlan(
        source="new!.MOV",
        duration=30.3,
        segments=[
            RefStyleEditSegment(
                start=27.56,
                end=29.12,
                text="Ссылка в шапке проверя.",
                semantic_role="cta",
                format_id="format_5_lower_demo_cta",
                subtitle_mode="plain_lower_demo",
                product_demo=True,
                reason="CTA link",
                confidence=0.92,
            ),
            RefStyleEditSegment(
                start=29.16,
                end=30.32,
                text="Пользуйся. Продолжение следует.",
                semantic_role="cta",
                format_id="format_5_lower_demo_cta",
                subtitle_mode="plain_lower_demo",
                product_demo=True,
                reason="CTA ending",
                confidence=0.90,
            ),
        ],
    )

    validated = validate_edit_plan(plan)

    assert len(validated.segments) == 1
    assert validated.segments[0].start == 27.56
    assert validated.segments[0].end == 30.32
    assert "Пользуйся" in validated.segments[0].text


def test_edit_plan_json_round_trip_preserves_director_reasons() -> None:
    plan = build_edit_plan(_semantic_transcript(), source="new!.MOV")

    restored = validate_edit_plan(plan.model_validate_json(plan.model_dump_json()))

    assert restored.segments[2].format_id == "format_4_blue_demo"
    assert "product" in restored.segments[2].reason.lower()
