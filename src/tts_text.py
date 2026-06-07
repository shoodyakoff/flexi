from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .assets import resolve_project_path

_TOKEN_RE = re.compile(r"[^\s]+", re.UNICODE)
_PRODUCT_BREAK_RE = re.compile(r"\{\{\s*product_break\s*\}\}", re.IGNORECASE)
_PAUSE_RE = re.compile(r"<<\s*(?P<legacy_seconds>\d+(?:[.,]\d+)?)\s*>>")
_PAUSE_MARKER_RE = re.compile(
    r"\{\{\s*pause\s*:\s*(?P<seconds>\d+(?:[.,]\d+)?)\s*\}\}",
    re.IGNORECASE,
)
_BREAK_TAG_RE = re.compile(
    r"<break\s+time=\"(?P<break_seconds>\d+(?:[.,]\d+)?)s\"\s*/?>",
    re.IGNORECASE,
)
_SLOW_START_RE = re.compile(r"\{\{\s*slow\s*\}\}", re.IGNORECASE)
_SLOW_END_RE = re.compile(r"\{\{\s*/slow\s*\}\}", re.IGNORECASE)
_ACCENT_RE = re.compile(r"\[\[\s*(?P<text>[^\[\]]+?)\s*\]\]")
_AUDIO_TAG_RE = re.compile(r"\[(?P<tag>[a-z][a-z -]*?)\]", re.IGNORECASE)
_STRESS_MARK_RE = re.compile("\u0301")
_V3_ALLOWED_AUDIO_TAGS = {
    "direct",
    "calm",
    "thinking",
    "pause",
    "slightly ironic",
    "matter-of-fact",
}
_SUBTITLE_TOKEN_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:ATS|айтиэс|эйтиэс|эй-ти-эс|эй ти эс)\b", re.IGNORECASE), "ATS"),
    (re.compile(r"\b(?:HR|эйчар)\b", re.IGNORECASE), "HR"),
]


@dataclass(frozen=True)
class VoiceoverTextPlan:
    tts_text: str
    align_text: str
    display_text: str
    product_break_after_token: int | None = None
    pronunciation_dictionary_hash: str | None = None


def _collapse_spaces(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([,.!?;:])", r"\1", collapsed)


def _marker_seconds(match: re.Match[str]) -> float:
    raw = (
        match.groupdict().get("seconds")
        or match.groupdict().get("legacy_seconds")
        or match.groupdict().get("break_seconds")
        or "0"
    )
    return float(raw.replace(",", "."))


def _format_break_seconds(seconds: float) -> str:
    clamped = max(0.0, min(3.0, seconds))
    formatted = f"{clamped:.2f}".rstrip("0").rstrip(".")
    return formatted or "0"


def _dictionary_path(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    return resolve_project_path(str(path))


def pronunciation_dictionary_hash(path: str | Path | None) -> str | None:
    resolved = _dictionary_path(path)
    if resolved is None:
        return None
    if not resolved.exists():
        return "missing"
    return hashlib.md5(resolved.read_bytes()).hexdigest()[:12]


def strip_product_breaks(text: str) -> str:
    return _collapse_spaces(_PRODUCT_BREAK_RE.sub(" ", text))


def strip_pause_markers(text: str) -> str:
    without_pauses = _PAUSE_MARKER_RE.sub(" ", text)
    without_pauses = _PAUSE_RE.sub(" ", without_pauses)
    return _collapse_spaces(_BREAK_TAG_RE.sub(" ", without_pauses))


def strip_accent_markers(text: str) -> str:
    return _ACCENT_RE.sub(lambda match: match.group("text"), text)


def strip_slow_markers(text: str) -> str:
    text = _SLOW_START_RE.sub(" ", text)
    return _collapse_spaces(_SLOW_END_RE.sub(" ", text))


def strip_audio_tags(text: str) -> str:
    return _collapse_spaces(_AUDIO_TAG_RE.sub(" ", text))


def strip_structural_markers(text: str) -> str:
    text = strip_accent_markers(text)
    text = strip_product_breaks(text)
    text = strip_pause_markers(text)
    text = strip_slow_markers(text)
    return strip_audio_tags(text)


def strip_stress_marks(text: str) -> str:
    return _STRESS_MARK_RE.sub("", text)


def _normalize_tts_words(
    text: str,
    *,
    pronunciation_dictionary: dict[str, str] | None = None,
) -> str:
    return _collapse_spaces(strip_accent_markers(text))


def compile_tts_markup(text: str, *, markup_dialect: str = "v2") -> str:
    """Compile authored voiceover markers into one TTS text payload."""

    def replace_pause(match: re.Match[str]) -> str:
        if markup_dialect == "v3":
            return " [pause] "
        return f' <break time="{_format_break_seconds(_marker_seconds(match))}s" /> '

    def filter_audio_tag(match: re.Match[str]) -> str:
        tag = _collapse_spaces(match.group("tag").lower())
        if markup_dialect == "v3" and tag in _V3_ALLOWED_AUDIO_TAGS:
            return f" [{tag}] "
        return " "

    compiled = strip_accent_markers(text)
    compiled = _SLOW_START_RE.sub(" ", compiled)
    compiled = _SLOW_END_RE.sub(" ", compiled)
    compiled = _PAUSE_MARKER_RE.sub(replace_pause, compiled)
    compiled = _PAUSE_RE.sub(replace_pause, compiled)
    compiled = _BREAK_TAG_RE.sub(replace_pause, compiled)
    compiled = _AUDIO_TAG_RE.sub(filter_audio_tag, compiled)
    return _collapse_spaces(compiled)


def split_product_breaks(text: str) -> list[str]:
    segments = [_collapse_spaces(part) for part in _PRODUCT_BREAK_RE.split(text)]
    return [segment for segment in segments if segment]


def product_break_after_token(text: str) -> int | None:
    match = _PRODUCT_BREAK_RE.search(text)
    if match is None:
        return None
    before = strip_structural_markers(text[: match.start()])
    return len(_TOKEN_RE.findall(before))


def prepare_tts_text(text: str) -> str:
    """Return plain TTS/alignment text without structural markers."""
    return _normalize_tts_words(strip_structural_markers(text))


def prepare_subtitle_text(text: str) -> str:
    """Return display text without pronunciation-only rewrites."""
    normalized = strip_stress_marks(strip_structural_markers(text))
    for pattern, replacement in _SUBTITLE_TOKEN_REPLACEMENTS:
        normalized = pattern.sub(replacement, normalized)
    return normalized


def prepare_voiceover_text(
    text: str,
    *,
    markup_dialect: str = "v2",
) -> VoiceoverTextPlan:
    tts_text = compile_tts_markup(text, markup_dialect=markup_dialect)
    align_text = strip_stress_marks(strip_structural_markers(tts_text))
    display_text = prepare_subtitle_text(text)
    return VoiceoverTextPlan(
        tts_text=tts_text,
        align_text=align_text,
        display_text=display_text,
        product_break_after_token=product_break_after_token(text),
    )
