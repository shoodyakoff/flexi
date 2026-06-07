from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .schemas import Transcript, Word


RefStyleFormatId = Literal[
    "format_1_hook_metal",
    "format_2_framed_face",
    "format_3_turn_badge",
    "format_4_blue_demo",
    "format_5_lower_demo_cta",
]

SemanticRole = Literal[
    "hook_problem",
    "product_intro",
    "product_demo",
    "workflow_explain",
    "cta",
    "support",
]

KNOWN_FORMATS: set[str] = {
    "format_1_hook_metal",
    "format_2_framed_face",
    "format_3_turn_badge",
    "format_4_blue_demo",
    "format_5_lower_demo_cta",
}

FORMAT_SUBTITLE_MODES: dict[RefStyleFormatId, str] = {
    "format_1_hook_metal": "metal_word",
    "format_2_framed_face": "pastry_script",
    "format_3_turn_badge": "badge_retro",
    "format_4_blue_demo": "poster_demo",
    "format_5_lower_demo_cta": "plain_lower_demo",
}

ROLE_FORMATS: dict[SemanticRole, RefStyleFormatId] = {
    "hook_problem": "format_1_hook_metal",
    "product_intro": "format_3_turn_badge",
    "product_demo": "format_4_blue_demo",
    "workflow_explain": "format_2_framed_face",
    "cta": "format_5_lower_demo_cta",
    "support": "format_2_framed_face",
}

MAX_SEGMENT_SEC = 7.2
MIN_SEGMENT_SEC = 0.85
MIN_VISUAL_SEGMENT_SEC = 2.2
PHRASE_GAP_SEC = 0.62
PUNCTUATION_RE = re.compile(r"[.!?…。;:]$")
TOKEN_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
SEMANTIC_BREAK_TERMS = {
    "поэтому",
    "тебе",
    "дальше",
    "после",
    "ссылка",
    "пользуйся",
}

HOOK_TERMS = {
    "устал",
    "устала",
    "надоело",
    "бесит",
    "сложно",
    "ручной",
    "ручная",
    "проблема",
    "проблем",
}
PRODUCT_INTRO_TERMS = {
    "сделал",
    "сделала",
    "собственный",
    "свой",
    "сервис",
    "поэтому",
    "решил",
}
PRODUCT_DEMO_TERMS = {
    "анализирует",
    "анализировать",
    "атс",
    "зайти",
    "загрузить",
    "загружаешь",
    "критерия",
    "критериям",
    "критерий",
    "критерию",
    "получить",
    "получаешь",
    "соответствие",
    "отчет",
    "отчёт",
    "вакансию",
    "вакансии",
    "демо",
    "экран",
}
WORKFLOW_TERMS = {
    "дальше",
    "потом",
    "после",
    "интервью",
    "отвечаешь",
    "адаптируем",
    "адаптировать",
    "пишем",
    "письмо",
    "сопроводительное",
}
CTA_TERMS = {
    "ссылка",
    "шапке",
    "шапка",
    "проверяй",
    "проверя",
    "пользуйся",
    "пользоваться",
    "переходи",
}


class RefStyleEditSegment(BaseModel):
    start: float
    end: float
    text: str
    semantic_role: SemanticRole
    format_id: RefStyleFormatId
    subtitle_mode: str
    product_demo: bool = False
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_time(self) -> "RefStyleEditSegment":
        if self.end <= self.start:
            raise ValueError("segment end must be greater than start")
        return self


class RefStyleEditPlan(BaseModel):
    source: str
    duration: float
    planning_mode: str = "llm_director_with_guardrails"
    segments: list[RefStyleEditSegment]
    warnings: list[str] = Field(default_factory=list)


def _tokens(text: str) -> set[str]:
    return {match.group(0).lower().replace("ё", "е") for match in TOKEN_RE.finditer(text)}


def _word_text(words: Iterable[Word]) -> str:
    return " ".join(word.word.strip() for word in words if word.word.strip())


def _normal_word(value: str) -> str:
    return value.strip().lower().replace("ё", "е").strip(" ,.!?;:…")


def _segment_words(transcript: Transcript) -> list[list[Word]]:
    words = sorted(transcript.words, key=lambda word: word.start)
    if not words:
        return []

    segments: list[list[Word]] = []
    current: list[Word] = [words[0]]

    for word in words[1:]:
        previous = current[-1]
        gap = word.start - previous.end
        current_duration = previous.end - current[0].start
        next_span = word.end - current[0].start
        word_is_semantic_break = _normal_word(word.word) in SEMANTIC_BREAK_TERMS
        should_break = (
            gap >= PHRASE_GAP_SEC
            or next_span > MAX_SEGMENT_SEC
            or word_is_semantic_break
            or bool(PUNCTUATION_RE.search(previous.word.strip()))
        )
        if should_break and previous.end - current[0].start >= MIN_SEGMENT_SEC:
            segments.append(current)
            current = [word]
        else:
            current.append(word)

    if current:
        if segments and current[-1].end - current[0].start < MIN_SEGMENT_SEC:
            segments[-1].extend(current)
        else:
            segments.append(current)

    return segments


def _contains(tokens: set[str], terms: set[str]) -> bool:
    normalized_terms = {term.replace("ё", "е") for term in terms}
    return bool(tokens & normalized_terms)


def _semantic_role(text: str, *, index: int, total: int) -> tuple[SemanticRole, str, float]:
    tokens = _tokens(text)
    near_end = total > 1 and index >= total - 1

    if index == 0 and _contains(tokens, HOOK_TERMS):
        return "hook_problem", "opening problem/emotion should use the hook metal format", 0.88
    if _contains(tokens, CTA_TERMS) or (near_end and {"ссылка", "пользуйся"} & tokens):
        return "cta", "CTA/link language should close with the lower-third product demo", 0.92
    if _contains(tokens, PRODUCT_DEMO_TERMS):
        return "product_demo", "product instruction terms call for the blue demo format", 0.86
    if _contains(tokens, WORKFLOW_TERMS):
        return "workflow_explain", "workflow terms need a calmer framed explanation beat", 0.78
    if _contains(tokens, PRODUCT_INTRO_TERMS):
        return "product_intro", "product reveal is a turn, not a generic demo step", 0.82
    if index == 0 or _contains(tokens, HOOK_TERMS):
        return "hook_problem", "opening problem/emotion should use the hook metal format", 0.84
    return "support", "supporting explanation falls back to the framed face format", 0.62


def _format_for_role(role: SemanticRole, previous_format: str | None) -> RefStyleFormatId:
    preferred = ROLE_FORMATS[role]
    return preferred


# Head-forward fallbacks used ONLY by the run-length guardrail to break a stretch
# of three+ identical formats. The reference keeps the creator on screen between
# demo steps, so a long product-demo run is interrupted by a framed-face (or turn
# badge) beat instead of staying on the product UI for the whole block.
RELIEF_FORMATS: dict[SemanticRole, tuple[RefStyleFormatId, ...]] = {
    "product_demo": ("format_2_framed_face", "format_3_turn_badge"),
    "workflow_explain": ("format_3_turn_badge", "format_2_framed_face"),
    "support": ("format_3_turn_badge", "format_2_framed_face"),
    "product_intro": ("format_2_framed_face", "format_4_blue_demo"),
}
_DEFAULT_RELIEF: tuple[RefStyleFormatId, ...] = (
    "format_2_framed_face",
    "format_3_turn_badge",
)


def _relief_format(
    role: SemanticRole, previous_format: str | None, relief_index: int
) -> RefStyleFormatId:
    """Pick a relief format that actually differs from the one being broken.

    ``_format_for_role`` returns the *preferred* format for a role, which is what
    created the long run in the first place; using it as the guardrail fallback
    was a no-op. This rotates through head-forward alternatives and never re-emits
    ``previous_format``.
    """
    options = RELIEF_FORMATS.get(role, _DEFAULT_RELIEF)
    choice = options[relief_index % len(options)]
    if choice == previous_format and len(options) > 1:
        choice = options[(relief_index + 1) % len(options)]
    return choice


def _segment_from_words(
    words: list[Word],
    *,
    index: int,
    total: int,
    previous_format: str | None,
) -> RefStyleEditSegment:
    text = _word_text(words)
    role, reason, confidence = _semantic_role(text, index=index, total=total)
    format_id = _format_for_role(role, previous_format)
    return RefStyleEditSegment(
        start=round(max(0.0, words[0].start), 3),
        end=round(max(words[-1].end, words[0].start + 0.34), 3),
        text=text,
        semantic_role=role,
        format_id=format_id,
        subtitle_mode=FORMAT_SUBTITLE_MODES[format_id],
        product_demo=format_id in {"format_4_blue_demo", "format_5_lower_demo_cta"},
        reason=reason,
        confidence=confidence,
    )


def build_director_prompt(transcript: Transcript) -> str:
    """Prompt contract for swapping the heuristic director with a real LLM call."""

    beat_lines = []
    for index, words in enumerate(_segment_words(transcript), start=1):
        beat_lines.append(
            f"{index}. {words[0].start:.2f}-{words[-1].end:.2f}: {_word_text(words)}"
        )
    formats = "\n".join(
        [
            "- format_1_hook_metal: emotional/problem hook",
            "- format_2_framed_face: calm explanation inside the yellow frame",
            "- format_3_turn_badge: reveal, turn, proof, emphasis",
            "- format_4_blue_demo: product instruction/demo process",
            "- format_5_lower_demo_cta: closing CTA/link with lower-third product demo",
        ]
    )
    return (
        "Choose a visual format for each transcript beat. Return strict JSON with "
        "segments containing start, end, semantic_role, format_id, reason, confidence.\n\n"
        f"Formats:\n{formats}\n\n"
        "Guardrails: use transcript meaning, avoid pure 1-2-3-4-5 rotation, keep product "
        "demo formats for actual product/process/CTA language, and avoid more than two "
        "same formats in a row.\n\n"
        "Transcript beats:\n"
        + "\n".join(beat_lines)
    )


def build_edit_plan(transcript: Transcript, *, source: str) -> RefStyleEditPlan:
    word_segments = _segment_words(transcript)
    segments: list[RefStyleEditSegment] = []
    previous_format: str | None = None
    for index, words in enumerate(word_segments):
        segment = _segment_from_words(
            words,
            index=index,
            total=len(word_segments),
            previous_format=previous_format,
        )
        segments.append(segment)
        previous_format = segment.format_id

    return validate_edit_plan(
        RefStyleEditPlan(
            source=source,
            duration=round(transcript.duration, 3),
            segments=segments,
        )
    )


def _coerce_format(format_id: str, role: SemanticRole) -> RefStyleFormatId:
    if format_id in KNOWN_FORMATS:
        return format_id  # type: ignore[return-value]
    return ROLE_FORMATS[role]


def _merge_segments(
    left: RefStyleEditSegment,
    right: RefStyleEditSegment,
    *,
    keep: RefStyleEditSegment,
) -> RefStyleEditSegment:
    format_id = keep.format_id
    return keep.model_copy(
        update={
            "start": round(min(left.start, right.start), 3),
            "end": round(max(left.end, right.end), 3),
            "text": f"{left.text.rstrip()} {right.text.lstrip()}".strip(),
            "format_id": format_id,
            "subtitle_mode": FORMAT_SUBTITLE_MODES[format_id],
            "product_demo": format_id
            in {"format_4_blue_demo", "format_5_lower_demo_cta"},
            "reason": f"{keep.reason}; merged short adjacent beat",
            "confidence": min(left.confidence, right.confidence),
        }
    )


def _merge_consecutive_cta_segments(
    segments: list[RefStyleEditSegment],
    warnings: list[str],
) -> list[RefStyleEditSegment]:
    merged: list[RefStyleEditSegment] = []
    for segment in segments:
        if (
            merged
            and merged[-1].semantic_role == "cta"
            and segment.semantic_role == "cta"
            and segment.start - merged[-1].end <= 0.35
        ):
            warnings.append(f"merged consecutive CTA beat at {segment.start:.2f}s")
            merged[-1] = _merge_segments(merged[-1], segment, keep=merged[-1])
        else:
            merged.append(segment)
    return merged


def _extend_segment_to_minimum(
    segments: list[RefStyleEditSegment],
    *,
    index: int,
) -> RefStyleEditSegment | None:
    segment = segments[index]
    needed = MIN_VISUAL_SEGMENT_SEC - (segment.end - segment.start)
    if needed <= 0:
        return segment

    next_start = segments[index + 1].start if index + 1 < len(segments) else None
    if next_start is not None and next_start - segment.end >= needed:
        return segment.model_copy(update={"end": round(segment.end + needed, 3)})

    prev_end = segments[index - 1].end if index > 0 else None
    if prev_end is not None and segment.start - prev_end >= needed:
        return segment.model_copy(update={"start": round(segment.start - needed, 3)})

    return None


def _fix_short_visual_segments(
    segments: list[RefStyleEditSegment],
    warnings: list[str],
) -> list[RefStyleEditSegment]:
    fixed = list(segments)
    index = 0
    while index < len(fixed):
        segment = fixed[index]
        duration = segment.end - segment.start
        if segment.semantic_role == "cta" or duration >= MIN_VISUAL_SEGMENT_SEC:
            index += 1
            continue

        if segment.semantic_role == "support" and index > 0:
            warnings.append(f"merged short support beat at {segment.start:.2f}s")
            fixed[index - 1] = _merge_segments(fixed[index - 1], segment, keep=fixed[index - 1])
            fixed.pop(index)
            continue

        extended = _extend_segment_to_minimum(fixed, index=index)
        if extended is not None:
            warnings.append(f"extended short beat at {segment.start:.2f}s")
            fixed[index] = extended
            index += 1
            continue

        if index + 1 < len(fixed):
            warnings.append(f"merged short beat at {segment.start:.2f}s into next beat")
            fixed[index + 1] = _merge_segments(segment, fixed[index + 1], keep=fixed[index + 1])
            fixed.pop(index)
            continue

        if index > 0:
            warnings.append(f"merged short beat at {segment.start:.2f}s into previous beat")
            fixed[index - 1] = _merge_segments(fixed[index - 1], segment, keep=fixed[index - 1])
            fixed.pop(index)
            continue

        index += 1

    return fixed


def validate_edit_plan(plan: RefStyleEditPlan) -> RefStyleEditPlan:
    warnings = list(plan.warnings)
    cleaned: list[RefStyleEditSegment] = []
    previous_end = 0.0
    previous_format: str | None = None
    repeat_count = 0
    relief_count = 0

    for raw in sorted(plan.segments, key=lambda segment: segment.start):
        start = round(max(0.0, raw.start), 3)
        end = round(max(raw.end, start + 0.34), 3)
        if start < previous_end - 0.01:
            start = round(previous_end, 3)
            end = round(max(end, start + 0.34), 3)
            warnings.append(f"shifted overlapping segment at {raw.start:.2f}s")
        if end - start > MAX_SEGMENT_SEC:
            end = round(start + MAX_SEGMENT_SEC, 3)
            warnings.append(f"clamped long segment at {start:.2f}s to {MAX_SEGMENT_SEC:.1f}s")

        format_id = _coerce_format(raw.format_id, raw.semantic_role)
        reason = raw.reason
        if format_id == previous_format:
            repeat_count += 1
        else:
            repeat_count = 1
        if repeat_count > 2:
            relief = _relief_format(raw.semantic_role, previous_format, relief_count)
            relief_count += 1
            repeat_count = 1
            warnings.append(f"broke {format_id} run at {start:.2f}s -> {relief}")
            reason = (
                f"relief beat: cut back to the head to break a long {format_id} "
                f"run ({raw.semantic_role})"
            )
            format_id = relief

        cleaned.append(
            raw.model_copy(
                update={
                    "start": start,
                    "end": end,
                    "format_id": format_id,
                    "subtitle_mode": FORMAT_SUBTITLE_MODES[format_id],
                    "product_demo": format_id
                    in {"format_4_blue_demo", "format_5_lower_demo_cta"},
                    "reason": reason,
                }
            )
        )
        previous_end = end
        previous_format = format_id

    cleaned = _merge_consecutive_cta_segments(cleaned, warnings)
    cleaned = _fix_short_visual_segments(cleaned, warnings)

    return RefStyleEditPlan(
        source=plan.source,
        duration=round(max(plan.duration, cleaned[-1].end if cleaned else 0.0), 3),
        planning_mode=plan.planning_mode,
        segments=cleaned,
        warnings=warnings,
    )
