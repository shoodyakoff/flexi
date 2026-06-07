from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
import json
import re
import subprocess
from pathlib import Path

from rich.console import Console

from .assets import resolve_project_path
from .schemas import (
    Config,
    SubtitleSafeBoxConfig,
    SubtitleStyle,
    SubtitleTrackDiagnostics,
    Transcript,
    Word,
)

# Keep only letters (any script), digits, and whitespace.
# Strips punctuation: . , ! ? : ; — - « » " ' ( ) … etc.
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

console = Console()
_DRAW_TEXT_WIDTH_RE = re.compile(r"\[Eval[^\]]*\]\s*([0-9.]+)")
_WORD_SPLIT_VOWELS = frozenset("АЕЁИОУЫЭЮЯ")
_STYLE_NAME = "Default"
_HELPER_TOKENS = frozenset(
    {
        "в",
        "во",
        "на",
        "к",
        "ко",
        "с",
        "со",
        "за",
        "по",
        "под",
        "над",
        "о",
        "об",
        "от",
        "до",
        "для",
        "из",
        "у",
        "при",
        "без",
        "про",
        "и",
        "а",
        "но",
        "же",
        "бы",
        "не",
        "я",
        "ты",
        "мы",
        "вы",
    }
)
_NUMBER_RE = re.compile(r"\d")
_MAX_LEAD_TIME_BEFORE_ACCENT_SEC = 0.5
_EDITORIAL_ACCENT_WORDS = frozenset(
    {
        "БЕСПЛАТНО",
        "БЕСПЛАТНЫЙ",
        "БЕСПЛАТНЫМ",
        "ОШИБКА",
        "ОШИБКИ",
        "ОФФЕР",
        "ОФФЕРА",
        "РЕЗЮМЕ",
        "ЭКСПЕРТ",
        "ЭКСПЕРТЫ",
        "УНИКАЛЬНОЙ",
        "УНИКАЛЬНАЯ",
        "КУРСЫ",
        "КУРСАМИ",
        "ПЛАТНЫМИ",
        "ДЕНЬГИ",
        "ДЕНЕГ",
        "РАБОТА",
        "ВАКАНСИЯ",
        "ВАКАНСИИ",
        "СОБЕСЕДОВАНИЕ",
    }
)


def _clean_word_text(text: str, uppercase: bool, strip_punct: bool) -> str:
    t = text.strip()
    if strip_punct:
        t = _strip_non_connecting_punctuation(t)
        t = t.replace("_", "")
    if uppercase:
        t = t.upper()
    return t.strip()


def _strip_non_connecting_punctuation(text: str) -> str:
    chars: list[str] = []
    last_index = len(text) - 1
    for index, char in enumerate(text):
        if char.isalnum() or char.isspace() or char == "_":
            chars.append(char)
            continue
        if (
            0 < index < last_index
            and text[index - 1].isalnum()
            and text[index + 1].isalnum()
        ):
            chars.append(char)
    return "".join(chars)


@dataclass(frozen=True)
class _ChunkLayout:
    display_text: str
    base_size_override: int | None
    warning: str | None = None


@dataclass(frozen=True)
class _SubtitleToken:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class _CaptionPlanToken:
    text: str
    start: float
    end: float
    role: str
    color: str
    size: str
    line: int
    x: int
    y: int


@dataclass(frozen=True)
class _CaptionPlanGroup:
    start: float
    end: float
    template: str
    zone: str
    tokens: list[_CaptionPlanToken]


def _is_number_token(token: _SubtitleToken | str) -> bool:
    text = token.text if isinstance(token, _SubtitleToken) else token
    return bool(_NUMBER_RE.search(text))


def _is_helper_token(token: _SubtitleToken | str) -> bool:
    text = token.text if isinstance(token, _SubtitleToken) else token
    return text.casefold() in _HELPER_TOKENS


def _is_content_token(token: _SubtitleToken | str) -> bool:
    return not _is_number_token(token) and not _is_helper_token(token)


def _content_word_count(tokens: list[_SubtitleToken]) -> int:
    return sum(1 for token in tokens if _is_content_token(token))


def _would_exceed_content_limit(
    chunk: list[_SubtitleToken],
    token: _SubtitleToken,
    style: SubtitleStyle,
) -> bool:
    if not style.forbid_two_content_words:
        return len(chunk) >= style.max_words_per_chunk
    return _content_word_count([*chunk, token]) > 1


def _is_attachable_token(token: _SubtitleToken, style: SubtitleStyle) -> bool:
    return (
        (style.attach_short_tokens and _is_helper_token(token))
        or (style.attach_numbers and _is_number_token(token))
    )


def _should_hold_for_next_content(
    token: _SubtitleToken,
    current: list[_SubtitleToken],
    next_token: _SubtitleToken | None,
    style: SubtitleStyle,
) -> bool:
    if not current or next_token is None:
        return False
    if not _is_attachable_token(token, style):
        return False
    if _content_word_count(current) == 0 or not _is_content_token(next_token):
        return False
    return not _would_exceed_content_limit([token], next_token, style)


def _normalize_token(value: str) -> str:
    return "".join(char for char in value.upper() if char.isalnum())


def _ass_alpha(value: int) -> str:
    return f"&H{max(0, min(255, value)):02X}&"


@lru_cache(maxsize=32)
def _resolve_font_path(fonts_dir: str, family: str) -> Path | None:
    directory = Path(fonts_dir)
    if not directory.exists():
        return None

    family_token = _normalize_token(family)
    candidates = sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".ttf", ".otf", ".ttc"}
    )
    for path in candidates:
        stem_token = _normalize_token(path.stem)
        if family_token and family_token in stem_token:
            return path
    for path in candidates:
        stem_token = _normalize_token(path.stem)
        if family_token.startswith(stem_token) or stem_token.startswith(family_token):
            return path
    return None


def _drawtext_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
    )


def _approximate_text_width(text: str, font_size: int, *, family: str) -> float:
    if not text:
        return 0.0
    is_wide_font = any(token in family.upper() for token in ("DRUK", "WIDE"))
    per_char = 0.92 if is_wide_font else 0.64
    space_factor = 0.38 if is_wide_font else 0.32
    width = 0.0
    for char in text:
        width += font_size * (space_factor if char.isspace() else per_char)
    return width


@lru_cache(maxsize=4096)
def _measure_text_width(font_path_str: str, font_size: int, outline: int, text: str) -> float:
    if not text:
        return 0.0

    filter_expr = (
        "drawtext="
        f"fontfile='{font_path_str}':"
        f"text='{_drawtext_escape(text)}':"
        f"fontsize={font_size}:"
        f"borderw={outline}:"
        "fontcolor=white:"
        "x=print(text_w):"
        "y=0"
    )
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "info",
            "-f",
            "lavfi",
            "-i",
            "color=size=1080x1920:duration=0.04:rate=1",
            "-vf",
            filter_expr,
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    output = f"{result.stderr}\n{result.stdout}"
    if result.returncode != 0:
        raise RuntimeError(output.strip() or "ffmpeg drawtext width probe failed")

    match = _DRAW_TEXT_WIDTH_RE.search(output)
    if match is None:
        raise RuntimeError("ffmpeg drawtext width probe did not emit text_w")
    return float(match.group(1))


def _text_width(
    cfg: Config | None,
    *,
    family: str,
    font_size: int,
    outline: int,
    text: str,
) -> float:
    if cfg is None:
        return _approximate_text_width(text, font_size, family=family)

    fonts_dir = str(resolve_project_path(cfg.fonts.directory).resolve())
    font_path = _resolve_font_path(fonts_dir, family)
    if font_path is None:
        return _approximate_text_width(text, font_size, family=family)
    try:
        return _measure_text_width(str(font_path), font_size, outline, text)
    except RuntimeError:
        return _approximate_text_width(text, font_size, family=family)


def _position_tag(safe_box: SubtitleSafeBoxConfig | None, *, y_offset_px: int = 0) -> str:
    if safe_box is None:
        return "{\\an8}"
    return f"{{\\an8\\pos({safe_box.center_x},{safe_box.top_padding_px + y_offset_px})}}"


def _accent_keywords(style: SubtitleStyle) -> set[str]:
    return {_normalize_token(keyword) for keyword in style.accent_keywords if keyword.strip()}


def _is_accent_chunk(chunk_words: list[str], style: SubtitleStyle) -> bool:
    if not (style.accent_font or style.accent_size or style.accent_color):
        return False

    keywords = _accent_keywords(style)
    for word in chunk_words:
        normalized = _normalize_token(word)
        if keywords and normalized in keywords:
            return True
        if style.accent_numbers and any(char.isdigit() for char in word):
            return True
        if style.accent_long_word_min_chars and len(normalized) >= style.accent_long_word_min_chars:
            return True
    return False


def _visual_tags(style: SubtitleStyle, *, accent: bool) -> str:
    if not accent:
        return ""

    tags: list[str] = []
    if style.accent_font:
        tags.append(f"\\fn{style.accent_font}")
    if style.accent_size:
        tags.append(f"\\fs{style.accent_size}")
    if style.accent_color:
        tags.append(f"\\1c{style.accent_color}")
    if style.accent_outline_color:
        tags.append(f"\\3c{style.accent_outline_color}")
    if style.accent_outline is not None:
        tags.append(f"\\bord{style.accent_outline}")
    if style.accent_shadow is not None:
        tags.append(f"\\shad{style.accent_shadow}")
    return "{" + "".join(tags) + "}" if tags else ""


def _animation_tags(style: SubtitleStyle) -> str:
    if style.animation != "pop":
        return ""

    enter_ms = style.pop_enter_ms
    settle_ms = style.pop_settle_ms
    settle_end_ms = enter_ms + settle_ms
    tags = [
        f"\\alpha{_ass_alpha(255)}",
        f"\\fscx{style.pop_start_scale}",
        f"\\fscy{style.pop_start_scale}",
    ]
    if enter_ms > 0:
        tags.append(
            f"\\t(0,{enter_ms},\\alpha{_ass_alpha(0)}"
            f"\\fscx{style.pop_overshoot_scale}\\fscy{style.pop_overshoot_scale})"
        )
    if settle_ms > 0:
        tags.append(
            f"\\t({enter_ms},{settle_end_ms},"
            f"\\fscx{style.pop_final_scale}\\fscy{style.pop_final_scale})"
        )
    return "{" + "".join(tags) + "}"


def _ghost_tags(style: SubtitleStyle, *, accent: bool) -> str:
    if not style.ghost_preflash:
        return ""

    tags = [
        f"\\alpha{_ass_alpha(style.ghost_alpha)}",
        "\\1c&H808080&",
        f"\\fscx{style.pop_start_scale}",
        f"\\fscy{style.pop_start_scale}",
    ]
    if style.blur:
        tags.append(f"\\blur{max(style.blur, 1.2):g}")
    if accent and style.accent_font:
        tags.append(f"\\fn{style.accent_font}")
    if accent and style.accent_size:
        tags.append(f"\\fs{style.accent_size}")
    return "{" + "".join(tags) + "}"


def _ghost_duration_sec(style: SubtitleStyle) -> float:
    return max(0.01, min(0.18, (style.pop_enter_ms or 80) / 1000))


def _token_size(style: SubtitleStyle, size_role: str) -> int:
    if size_role == "huge":
        return style.accent_size or round(style.size * 1.55)
    if size_role == "large":
        return round(style.size * 1.18)
    if size_role == "small":
        return round(style.size * 0.74)
    return style.size


def _manual_accent_words(style: SubtitleStyle) -> set[str]:
    return {_normalize_token(word) for word in style.accent_keywords if word.strip()}


def _manual_accent_phrases(style: SubtitleStyle) -> set[str]:
    return {
        " ".join(_normalize_token(part) for part in phrase.split() if part.strip())
        for phrase in style.accent_phrases
        if phrase.strip()
    }


def _is_editorial_accent_candidate(token: _SubtitleToken, style: SubtitleStyle) -> bool:
    normalized = _normalize_token(token.text)
    if normalized in _manual_accent_words(style):
        return True
    if style.accent_numbers and _is_number_token(token):
        return True
    return normalized in _EDITORIAL_ACCENT_WORDS


def _editorial_group_tokens(tokens: list[_SubtitleToken], style: SubtitleStyle) -> list[list[_SubtitleToken]]:
    groups: list[list[_SubtitleToken]] = []
    current: list[_SubtitleToken] = []
    max_words = max(1, min(4, style.max_words_per_chunk))
    max_duration = max(0.5, style.max_chunk_duration_sec)
    max_gap = max(0.12, style.max_chunk_gap_sec)

    for token in tokens:
        if not current:
            current = [token]
            continue

        previous = current[-1]
        gap = token.start - previous.end
        duration = token.end - current[0].start
        current_has_accent = any(_is_editorial_accent_candidate(item, style) for item in current)
        token_is_accent = _is_editorial_accent_candidate(token, style)
        accent_arrives_late = (
            token_is_accent
            and not current_has_accent
            and _content_word_count(current) > 0
            and token.start - current[0].start > _MAX_LEAD_TIME_BEFORE_ACCENT_SEC
        )
        would_overfill_content = _content_word_count([*current, token]) > 2 and not _is_helper_token(token)
        should_split = (
            gap > max_gap
            or len(current) >= max_words
            or duration > max_duration
            or accent_arrives_late
            or current_has_accent and token_is_accent
            or would_overfill_content and len(current) >= 2
        )
        if should_split:
            if _is_helper_token(current[-1]) and _is_content_token(token) and len(current) > 1:
                groups.append(current[:-1])
                current = [current[-1], token]
            else:
                groups.append(current)
                current = [token]
        else:
            current.append(token)

    if current:
        groups.append(current)
    return groups


def _accent_index_for_group(group: list[_SubtitleToken], style: SubtitleStyle) -> int | None:
    manual_words = _manual_accent_words(style)
    for index, token in enumerate(group):
        if _normalize_token(token.text) in manual_words:
            return index
    for index, token in enumerate(group):
        if style.accent_numbers and _is_number_token(token):
            return index
    for index, token in enumerate(group):
        if _normalize_token(token.text) in _EDITORIAL_ACCENT_WORDS:
            return index
    content_indexes = [index for index, token in enumerate(group) if _is_content_token(token)]
    if len(group) == 1 and content_indexes:
        return 0
    if content_indexes and len(group) >= 2:
        return content_indexes[-1]
    return None


def _phrase_accent_indexes(group: list[_SubtitleToken], style: SubtitleStyle) -> set[int]:
    phrases = _manual_accent_phrases(style)
    if not phrases:
        return set()
    normalized = [_normalize_token(token.text) for token in group]
    indexes: set[int] = set()
    for start in range(len(normalized)):
        for end in range(start + 2, len(normalized) + 1):
            if " ".join(normalized[start:end]) in phrases:
                indexes.update(range(start, end))
    return indexes


def _template_for_group(group: list[_SubtitleToken], accent_indexes: set[int]) -> str:
    if len(group) == 1:
        return "single_big"
    if 0 in accent_indexes:
        return "big_over_small"
    if len(group) >= 3:
        return "stack_3"
    return "small_over_big"


def _zone_for_caption_group(section: str) -> str:
    if section in {"hook", "cta"}:
        return "bottom_third_center"
    return "top_center"


def _base_position_for_zone(
    safe_box: SubtitleSafeBoxConfig | None,
    zone: str,
) -> tuple[int, int]:
    if safe_box is None:
        if zone == "bottom_third_center":
            return 540, 1600
        return 540, 360
    if zone == "bottom_third_center":
        return safe_box.center_x, round(safe_box.play_res_y * 5 / 6)
    if zone == "chest_center":
        return safe_box.center_x, max(safe_box.top_padding_px + 300, round(safe_box.play_res_y * 0.55))
    if zone == "lower_center":
        return safe_box.center_x, max(safe_box.top_padding_px, safe_box.play_res_y - safe_box.bottom_padding_px - 160)
    return safe_box.center_x, safe_box.top_padding_px + 110


def _attach_trailing_helpers_to_accent_lines(
    line_specs: list[tuple[list[int], int, str]],
    group: list[_SubtitleToken],
    accent_indexes: set[int],
) -> list[tuple[list[int], int, str]]:
    adjusted = [(list(indexes), y_offset, line_size) for indexes, y_offset, line_size in line_specs]
    for index in range(len(adjusted) - 1):
        current_indexes = adjusted[index][0]
        next_indexes = adjusted[index + 1][0]
        if not current_indexes or not next_indexes or next_indexes[0] not in accent_indexes:
            continue

        trailing_helpers: list[int] = []
        while current_indexes and _is_helper_token(group[current_indexes[-1]]):
            trailing_helpers.insert(0, current_indexes.pop())
        if trailing_helpers:
            next_indexes[:0] = trailing_helpers
    return adjusted


def _visual_indexes_for_line(
    indexes: list[int],
    group: list[_SubtitleToken],
    accent_indexes: set[int],
) -> list[list[int]]:
    visual_indexes: list[list[int]] = []
    support_run: list[int] = []
    for index in indexes:
        if index in accent_indexes:
            trailing_helpers: list[int] = []
            while support_run and _is_helper_token(group[support_run[-1]]):
                trailing_helpers.insert(0, support_run.pop())
            if support_run:
                visual_indexes.append(support_run)
                support_run = []
            visual_indexes.append([*trailing_helpers, index])
        else:
            support_run.append(index)
    if support_run:
        visual_indexes.append(support_run)
    return visual_indexes


def _caption_token_layout(
    group: list[_SubtitleToken],
    *,
    style: SubtitleStyle,
    safe_box: SubtitleSafeBoxConfig | None,
    section: str,
    cfg: Config | None,
) -> _CaptionPlanGroup:
    zone = _zone_for_caption_group(section)
    x, y = _base_position_for_zone(safe_box, zone)
    phrase_indexes = _phrase_accent_indexes(group, style)
    primary_accent = _accent_index_for_group(group, style)
    accent_indexes = phrase_indexes or ({primary_accent} if primary_accent is not None else set())
    template = _template_for_group(group, accent_indexes)

    if template == "single_big":
        line_specs = [([0], 0, "huge")]
    elif template == "small_over_big":
        accent = min(accent_indexes) if accent_indexes else len(group) - 1
        line_specs = [
            ([index for index in range(len(group)) if index < accent], -44, "small"),
            ([index for index in range(len(group)) if index >= accent], 34, "huge"),
        ]
    elif template == "big_over_small":
        accent = max(accent_indexes) if accent_indexes else 0
        line_specs = [
            ([index for index in range(len(group)) if index <= accent], -34, "huge"),
            ([index for index in range(len(group)) if index > accent], 44, "small"),
        ]
    else:
        if accent_indexes:
            first_accent = min(accent_indexes)
            last_accent = max(accent_indexes)
            line_specs = [
                ([index for index in range(len(group)) if index < first_accent], -54, "small"),
                ([index for index in range(len(group)) if first_accent <= index <= last_accent], 28, "huge"),
                ([index for index in range(len(group)) if index > last_accent], 94, "small"),
            ]
        elif len(group) == 3:
            line_specs = [
                ([0, 1], -44, "small"),
                ([2], 44, "huge"),
            ]
        else:
            line_specs = [
                ([0], -76, "small"),
                ([1, 2] if len(group) > 2 else [1], 0, "large"),
                (list(range(3, len(group))), 76, "huge"),
            ]

    line_specs = _attach_trailing_helpers_to_accent_lines(line_specs, group, accent_indexes)
    plan_tokens: list[_CaptionPlanToken] = []
    for line_index, (indexes, y_offset, line_size) in enumerate(line_specs):
        indexes = [index for index in indexes if 0 <= index < len(group)]
        if not indexes:
            continue
        visual_indexes = _visual_indexes_for_line(indexes, group, accent_indexes)

        line_items: list[tuple[list[int], bool, str, str, float]] = []
        for item_indexes in visual_indexes:
            is_accent = any(index in accent_indexes for index in item_indexes)
            text = " ".join(group[index].text for index in item_indexes)
            size = "huge" if is_accent and line_size != "small" else line_size
            token = group[item_indexes[0]]
            family = style.accent_font if is_accent and style.accent_font else style.font
            outline = (
                style.accent_outline
                if is_accent and style.accent_outline is not None
                else style.outline
            )
            width = _text_width(
                cfg,
                family=family,
                font_size=_token_size(style, size),
                outline=outline,
                text=text,
            )
            line_items.append((item_indexes, is_accent, size, text, width))
        gap_px = round(style.size * 0.12)
        total_width = sum(width for *_, width in line_items) + gap_px * max(0, len(line_items) - 1)
        cursor_x = x - total_width / 2
        for item_indexes, is_accent, size, text, width in line_items:
            first_token = group[item_indexes[0]]
            last_token = group[item_indexes[-1]]
            token_x = round(cursor_x + width / 2)
            cursor_x += width + gap_px
            plan_tokens.append(
                _CaptionPlanToken(
                    text=text,
                    start=first_token.start,
                    end=last_token.end,
                    role="accent" if is_accent else "support",
                    color="yellow" if is_accent else "white",
                    size=size,
                    line=line_index,
                    x=token_x,
                    y=y + y_offset,
                )
            )

    group_end = max(group[-1].end + style.end_hold_sec, group[0].start + style.min_display_duration_sec)
    return _CaptionPlanGroup(
        start=group[0].start,
        end=group_end,
        template=template,
        zone=zone,
        tokens=plan_tokens,
    )


def _editorial_caption_plan(
    transcript: Transcript,
    style: SubtitleStyle,
    *,
    safe_box: SubtitleSafeBoxConfig | None,
    section: str,
    cfg: Config | None,
) -> list[_CaptionPlanGroup]:
    tokens = _subtitle_tokens(transcript, style)
    groups = _editorial_group_tokens(tokens, style)
    plan_groups = [
        _caption_token_layout(group, style=style, safe_box=safe_box, section=section, cfg=cfg)
        for group in groups
    ]
    adjusted: list[_CaptionPlanGroup] = []
    for index, group in enumerate(plan_groups):
        end = group.end
        if index + 1 < len(plan_groups):
            end = min(end, max(group.start + 0.01, plan_groups[index + 1].start - 0.01))
        adjusted.append(
            _CaptionPlanGroup(
                start=group.start,
                end=end,
                template=group.template,
                zone=group.zone,
                tokens=group.tokens,
            )
        )
    return adjusted


def _caption_plan_payload(groups: list[_CaptionPlanGroup]) -> dict:
    return {
        "groups": [
            {
                "start": round(group.start, 3),
                "end": round(group.end, 3),
                "template": group.template,
                "zone": group.zone,
                "tokens": [
                    {
                        "text": token.text,
                        "start": round(token.start, 3),
                        "end": round(token.end, 3),
                        "role": token.role,
                        "color": token.color,
                        "size": token.size,
                        "line": token.line,
                        "x": token.x,
                        "y": token.y,
                    }
                    for token in group.tokens
                ],
            }
            for group in groups
        ]
    }


def _editorial_token_prefix(style: SubtitleStyle, token: _CaptionPlanToken) -> str:
    color = style.accent_color if token.color == "yellow" else style.primary_color
    font = style.accent_font if token.role == "accent" and style.accent_font else style.font
    outline = style.accent_outline if token.role == "accent" and style.accent_outline is not None else style.outline
    shadow = style.accent_shadow if token.role == "accent" and style.accent_shadow is not None else style.shadow
    outline_color = style.accent_outline_color if token.role == "accent" and style.accent_outline_color else style.outline_color
    return (
        f"{{\\an5\\pos({token.x},{token.y})"
        f"\\fn{font}\\fs{_token_size(style, token.size)}"
        f"\\1c{color}\\3c{outline_color}\\bord{outline}\\shad{shadow}}}"
    )


def _write_editorial_ass(
    transcript: Transcript,
    style: SubtitleStyle,
    out_path: Path,
    *,
    safe_box: SubtitleSafeBoxConfig | None,
    section: str,
    caption_plan_path: Path | None,
    cfg: Config | None,
) -> Path:
    header = _ass_header(style, safe_box=safe_box)
    groups = _editorial_caption_plan(transcript, style, safe_box=safe_box, section=section, cfg=cfg)
    events: list[str] = []
    for group in groups:
        for token in group.tokens:
            prefix = _editorial_token_prefix(style, token)
            if style.blur:
                prefix = f"{prefix}{{\\blur{style.blur}}}"
            prefix = f"{prefix}{_animation_tags(style)}"
            if style.ghost_preflash:
                ghost = (
                    f"{{\\an5\\pos({token.x},{token.y + style.ghost_offset_px})}}"
                    f"{_ghost_tags(style, accent=token.role == 'accent')}"
                )
                events.append(
                    _dialogue_line(
                        token.start,
                        min(group.end, token.start + _ghost_duration_sec(style)),
                        ghost,
                        token.text,
                        layer=0,
                    )
                )
            events.append(
                _dialogue_line(
                    token.start,
                    group.end,
                    prefix,
                    token.text,
                    layer=1,
                )
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    if caption_plan_path is not None:
        caption_plan_path.write_text(
            json.dumps(_caption_plan_payload(groups), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    console.log(f"[green]✓[/green] Subtitles saved: {out_path}")
    return out_path



def _style_line(
    *,
    font: str,
    size: int,
    primary_color: str,
    outline_color: str,
    outline: int,
    shadow: int,
    margin_v: int,
) -> str:
    return (
        f"Style: {_STYLE_NAME},{font},{size},{primary_color},&H000000FF,{outline_color},&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},{shadow},8,10,10,{margin_v},1"
    )


def _ass_header(
    style: SubtitleStyle,
    safe_box: SubtitleSafeBoxConfig | None = None,
    title: str = "Content Factory",
) -> str:
    # ASS colour format: &HAABBGGRR (alpha=00 → fully opaque).
    # We render at the top edge of the safe box; alignment 8 = top center.
    play_res_x = safe_box.play_res_x if safe_box is not None else 1080
    play_res_y = safe_box.play_res_y if safe_box is not None else 1920
    return f"""[Script Info]
Title: {title}
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
Collisions: Normal

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{_style_line(font=style.font, size=style.size, primary_color=style.primary_color, outline_color=style.outline_color, outline=style.outline, shadow=style.shadow, margin_v=style.margin_v)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ts_interval(start: float, end: float) -> tuple[str, str]:
    start_cs = max(0, round(start * 100))
    end_cs = max(0, round(end * 100))
    if end_cs <= start_cs:
        end_cs = start_cs + 1
    return _ts(start_cs / 100), _ts(end_cs / 100)


def _dialogue_line(start: float, end: float, prefix: str, text_line: str, *, layer: int = 0) -> str:
    start_ts, end_ts = _ts_interval(start, end)
    return f"Dialogue: {layer},{start_ts},{end_ts},{_STYLE_NAME},,0,0,0,,{prefix}{text_line}"


def _safe_width(safe_box: SubtitleSafeBoxConfig | None) -> int | None:
    if safe_box is None:
        return None
    return max(1, safe_box.play_res_x - (safe_box.side_padding_px * 2))


def _word_split_candidates(text: str) -> list[int]:
    if len(text) < 6 or not text.isalpha():
        return []

    min_side = max(3, len(text) // 4)
    midpoint = len(text) / 2
    scored: list[tuple[float, int]] = []
    for index in range(min_side, len(text) - min_side + 1):
        left = text[index - 1]
        right = text[index]
        if left in {"Ь", "Ъ"} or right in {"Ь", "Ъ"}:
            continue

        score = abs(index - midpoint)
        if left in _WORD_SPLIT_VOWELS and right not in _WORD_SPLIT_VOWELS:
            score -= 0.45
        elif left not in _WORD_SPLIT_VOWELS and right in _WORD_SPLIT_VOWELS:
            score -= 0.15
        scored.append((score, index))
    return [index for _, index in sorted(scored)[:8]]


def _best_split_phrase(
    words: list[str],
    *,
    family: str,
    font_size: int,
    outline: int,
    cfg: Config | None,
) -> tuple[str, float] | None:
    if len(words) < 2:
        return None

    best_choice: tuple[str, float] | None = None
    best_score: tuple[float, float, int] | None = None
    for split_index in range(1, len(words)):
        first_line = " ".join(words[:split_index])
        second_line = " ".join(words[split_index:])
        first_width = _text_width(
            cfg,
            family=family,
            font_size=font_size,
            outline=outline,
            text=first_line,
        )
        second_width = _text_width(
            cfg,
            family=family,
            font_size=font_size,
            outline=outline,
            text=second_line,
        )
        score = (
            max(first_width, second_width),
            abs(first_width - second_width),
            abs(split_index - len(words) // 2),
        )
        if best_score is None or score < best_score:
            best_score = score
            best_choice = (f"{first_line}\\N{second_line}", max(first_width, second_width))
    return best_choice


def _best_split_chunk_word(
    words: list[str],
    *,
    family: str,
    font_size: int,
    outline: int,
    cfg: Config | None,
) -> tuple[str, float] | None:
    best_choice: tuple[str, float] | None = None
    best_score: tuple[float, float, int, float] | None = None

    for word_index, word in enumerate(words):
        if len(word) < 6:
            continue
        for split_index in _word_split_candidates(word):
            first_parts = [*words[:word_index], word[:split_index]]
            second_parts = [word[split_index:], *words[word_index + 1:]]
            if not first_parts or not second_parts:
                continue

            first_line = " ".join(first_parts)
            second_line = " ".join(second_parts)
            first_width = _text_width(
                cfg,
                family=family,
                font_size=font_size,
                outline=outline,
                text=first_line,
            )
            second_width = _text_width(
                cfg,
                family=family,
                font_size=font_size,
                outline=outline,
                text=second_line,
            )
            score = (
                max(first_width, second_width),
                abs(first_width - second_width),
                abs(len(first_parts) - len(second_parts)),
                abs(split_index - (len(word) / 2)),
            )
            if best_score is None or score < best_score:
                best_score = score
                best_choice = (
                    f"{first_line}\\N{second_line}",
                    max(first_width, second_width),
                )
    return best_choice


def _layout_chunk(
    chunk_words: list[str],
    *,
    style: SubtitleStyle,
    safe_box: SubtitleSafeBoxConfig | None,
    cfg: Config | None,
) -> _ChunkLayout:
    word_text = " ".join(chunk_words)
    width = _text_width(
        cfg,
        family=style.font,
        font_size=style.size,
        outline=style.outline,
        text=word_text,
    )
    safe_width = _safe_width(safe_box)
    display_text = word_text

    if (
        safe_width is not None
        and width > safe_width
        and style.max_lines > 1
        and len(chunk_words) > 1
    ):
        split_choice = _best_split_phrase(
            chunk_words,
            family=style.font,
            font_size=style.size,
            outline=style.outline,
            cfg=cfg,
        )
        if split_choice is not None:
            split_text, split_width = split_choice
            if split_width <= safe_width or (safe_width / width if width > 0 else 1.0) < style.min_font_scale:
                display_text = split_text
                width = split_width

    if (
        safe_width is not None
        and width > safe_width
        and style.split_long_words
        and style.max_lines > 1
    ):
        required_ratio = safe_width / width if width > 0 else 1.0
        should_try_split = (
            any(len(word) >= style.long_word_split_min_chars for word in chunk_words)
            or required_ratio < 0.65
        )
        if should_try_split:
            split_choice = _best_split_chunk_word(
                chunk_words,
                family=style.font,
                font_size=style.size,
                outline=style.outline,
                cfg=cfg,
            )
            if split_choice is not None:
                split_text, split_width = split_choice
                if split_width <= safe_width or required_ratio < style.min_font_scale:
                    display_text = split_text
                    width = split_width

    if safe_width is None or width <= safe_width:
        return _ChunkLayout(display_text=display_text, base_size_override=None)

    scale_ratio = safe_width / width if width > 0 else 1.0
    base_size_override: int | None = max(1, round(style.size * scale_ratio))
    if base_size_override == style.size:
        base_size_override = None

    warning = None
    if scale_ratio < style.min_font_scale:
        warning = (
            f"subtitle auto-fit compacted '{word_text}' to {scale_ratio:.2f}x "
            f"to stay inside the safe box"
        )
    return _ChunkLayout(
        display_text=display_text,
        base_size_override=base_size_override,
        warning=warning,
    )


def _subtitle_tokens(transcript: Transcript, style: SubtitleStyle) -> list[_SubtitleToken]:
    tokens: list[_SubtitleToken] = []
    for word in transcript.words:
        text = _clean_word_text(word.word, style.uppercase, style.strip_punctuation)
        if not text:
            continue
        tokens.append(_SubtitleToken(text=text, start=word.start, end=word.end))
    return tokens


def _chunk_tokens(tokens: list[_SubtitleToken], style: SubtitleStyle) -> list[list[_SubtitleToken]]:
    chunks: list[list[_SubtitleToken]] = []
    current: list[_SubtitleToken] = []

    for index, token in enumerate(tokens):
        if not current:
            current = [token]
            continue

        previous = current[-1]
        chunk_start = current[0].start
        gap = token.start - previous.end
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        would_fit_count = (
            not _would_exceed_content_limit(current, token, style)
            if style.forbid_two_content_words
            else len(current) < style.max_words_per_chunk
        )
        would_fit_gap = gap <= style.max_chunk_gap_sec
        would_fit_duration = token.end - chunk_start <= style.max_chunk_duration_sec

        if (
            _should_hold_for_next_content(token, current, next_token, style)
            or not _is_attachable_token(token, style)
            and style.forbid_two_content_words
            and _content_word_count(current) >= 1
        ):
            chunks.append(current)
            current = [token]
        elif would_fit_count and would_fit_gap and would_fit_duration:
            current.append(token)
        else:
            chunks.append(current)
            current = [token]

    if current:
        chunks.append(current)
    return chunks


def _chunk_intervals(
    chunks: list[list[_SubtitleToken]],
    style: SubtitleStyle,
) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    previous_end = 0.0

    for index, chunk in enumerate(chunks):
        start = max(chunk[0].start, previous_end)
        natural_end = max(chunk[-1].end, start)
        desired_end = max(natural_end + style.end_hold_sec, start + style.min_display_duration_sec)
        if index + 1 < len(chunks):
            next_start = max(chunks[index + 1][0].start, start + 0.01)
            latest_end = max(start + 0.01, next_start - 0.01)
            end = min(desired_end, latest_end)
        else:
            end = desired_end
        if end <= start:
            end = start + 0.01
        intervals.append((start, end))
        previous_end = end

    return intervals


def _coverage_warning(
    tokens: list[_SubtitleToken],
    chunks: list[list[_SubtitleToken]],
) -> str | None:
    expected = [token.text for token in tokens]
    rendered = [token.text for chunk in chunks for token in chunk]
    if expected == rendered:
        return None
    return (
        "subtitle coverage mismatch: "
        f"expected {len(expected)} word token(s), rendered {len(rendered)}"
    )


def subtitle_diagnostics(
    transcript: Transcript,
    style: SubtitleStyle,
    *,
    label: str,
    layout_warnings: list[str] | None = None,
) -> SubtitleTrackDiagnostics:
    tokens = _subtitle_tokens(transcript, style)
    chunks = _chunk_tokens(tokens, style)
    rendered = [token.text for chunk in chunks for token in chunk]
    expected = [token.text for token in tokens]
    intervals = _chunk_intervals(chunks, style)
    short_event_count = sum(
        1 for start, end in intervals
        if end - start < style.min_event_duration_warning_sec
    )
    auto_fit_events = 0
    if layout_warnings:
        auto_fit_events = sum(
            1 for warning in layout_warnings
            if warning.startswith("subtitle auto-fit compacted")
        )
    return SubtitleTrackDiagnostics(
        label=label,
        transcript_tokens=len(expected),
        rendered_tokens=len(rendered),
        missing_tokens=0 if expected == rendered else max(0, len(expected) - len(rendered)),
        short_event_count=short_event_count,
        double_content_word_chunks=sum(
            1 for chunk in chunks
            if _content_word_count(chunk) > 1
        ),
        auto_fit_events=auto_fit_events,
    )


def transcript_to_ass(
    transcript: Transcript,
    style: SubtitleStyle,
    out_path: Path,
    *,
    safe_box: SubtitleSafeBoxConfig | None = None,
    cfg: Config | None = None,
    layout_warnings: list[str] | None = None,
    caption_plan_path: Path | None = None,
    section: str = "body",
) -> Path:
    if style.caption_mode == "editorial":
        return _write_editorial_ass(
            transcript,
            style,
            out_path,
            safe_box=safe_box,
            section=section,
            caption_plan_path=caption_plan_path,
            cfg=cfg,
        )

    header = _ass_header(style, safe_box=safe_box)
    events: list[str] = []
    warnings: list[str] = []

    base_prefix = _position_tag(safe_box)

    tokens = _subtitle_tokens(transcript, style)
    chunks = _chunk_tokens(tokens, style)
    coverage_warning = _coverage_warning(tokens, chunks)
    if coverage_warning is not None:
        warnings.append(coverage_warning)
    double_content_chunks = sum(1 for chunk in chunks if _content_word_count(chunk) > 1)
    if double_content_chunks:
        warnings.append(
            f"subtitle double-content chunks: {double_content_chunks}"
        )
    intervals = _chunk_intervals(chunks, style)
    short_events = sum(
        1 for start, end in intervals
        if end - start < style.min_event_duration_warning_sec
    )
    if short_events:
        warnings.append(
            f"subtitle short events below {style.min_event_duration_warning_sec:.2f}s: {short_events}"
        )

    for index, chunk in enumerate(chunks):
        chunk_words = [token.text for token in chunk]
        accent = _is_accent_chunk(chunk_words, style)
        layout_style = style
        if accent:
            layout_style = style.model_copy(
                update={
                    "font": style.accent_font or style.font,
                    "size": style.accent_size or style.size,
                    "outline": style.accent_outline if style.accent_outline is not None else style.outline,
                }
            )
        layout = _layout_chunk(chunk_words, style=layout_style, safe_box=safe_box, cfg=cfg)
        if layout.warning is not None:
            warnings.append(layout.warning)
        prefix = f"{base_prefix}{_visual_tags(style, accent=accent)}"
        if style.fade_in_ms or style.fade_out_ms:
            prefix = f"{prefix}{{\\fad({style.fade_in_ms},{style.fade_out_ms})}}"
        if style.blur:
            prefix = f"{prefix}{{\\blur{style.blur}}}"
        if layout.base_size_override is not None:
            prefix = f"{prefix}{{\\fs{layout.base_size_override}}}"
        prefix = f"{prefix}{_animation_tags(style)}"
        start, end = intervals[index]
        if style.ghost_preflash:
            ghost_prefix = (
                f"{_position_tag(safe_box, y_offset_px=style.ghost_offset_px)}"
                f"{_ghost_tags(style, accent=accent)}"
            )
            events.append(
                _dialogue_line(
                    start,
                    min(end, start + _ghost_duration_sec(style)),
                    ghost_prefix,
                    layout.display_text,
                    layer=0,
                )
            )
        events.append(
            _dialogue_line(
                start,
                end,
                prefix,
                layout.display_text,
                layer=1 if style.ghost_preflash or style.animation != "none" else 0,
            )
        )

    content = header + "\n".join(events) + "\n"

    if layout_warnings is not None:
        layout_warnings.extend(warnings)
    for warning in warnings:
        console.log(f"[yellow]![/yellow] {warning}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    console.log(f"[green]✓[/green] Subtitles saved: {out_path}")
    return out_path
