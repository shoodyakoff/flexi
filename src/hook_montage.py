from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
import subprocess

from rich.console import Console

from .assets import load_asset_meta, resolve_project_path
from .assembly import (
    _COLOR_OUT_TAGS,
    _X264_COLOR_PARAMS,
    _probe_duration,
    _run,
    _spoken_audio_filter,
    _video_normalize_chain,
)
from .schemas import (
    AssetEntry,
    Config,
    HookMontageDiagnostics,
    HookMontageOverlayDiagnostics,
    HookMontageOverlaysConfig,
    HookMontageSfxDiagnostics,
    HookMontageTimeRange,
    HookOverlayPlacement,
    SubtitleStyle,
    Transcript,
    Word,
)
from .subtitles import (
    _SubtitleToken,
    _ass_header,
    _chunk_intervals,
    _chunk_tokens,
    _dialogue_line,
    _layout_chunk,
    _subtitle_tokens,
    transcript_to_ass,
)
from .text_matching import matches_anchor_root, matches_overlay_anchor

console = Console()


_TEXT_ONLY_ACCENT_FILE = Path("__text_only_accent__")
_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")


@dataclass(frozen=True)
class HookAccentCue:
    keyword: str
    word: str
    word_start_sec: float
    word_end_sec: float
    hit_sec: float
    file: Path
    placement: HookOverlayPlacement
    width_px: int
    opacity: float
    enter_sec: float
    exit_sec: float
    display_duration_sec: float


@dataclass(frozen=True)
class HookOverlayEvent:
    keyword: str
    word: str
    word_start_sec: float
    word_end_sec: float
    file: Path
    placement: HookOverlayPlacement
    start_sec: float
    peak_sec: float
    end_sec: float
    width_px: int
    opacity: float
    enter_sec: float
    exit_sec: float


@dataclass(frozen=True)
class HookSfxEvent:
    kind: str
    file: Path
    timeline_start_sec: float
    trim_start_sec: float
    duration_sec: float
    gain_db: float
    peak_target_sec: float | None = None


@dataclass(frozen=True)
class HookMontagePlan:
    source_duration_sec: float
    final_duration_sec: float
    kept_ranges: list[tuple[float, float]]
    removed_ranges: list[tuple[float, float]]
    transcript: Transcript
    accent_cues: list[HookAccentCue]
    overlays: list[HookOverlayEvent]
    sfx_events: list[HookSfxEvent]
    motion_events: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class HookMontageResult:
    video_path: Path
    subtitle_path: Path
    transcript: Transcript
    diagnostics: HookMontageDiagnostics
    subtitle_warnings: list[str]


def _round_time(value: float) -> float:
    return round(max(0.0, value), 3)


def _time_range(start: float, end: float) -> HookMontageTimeRange:
    return HookMontageTimeRange(start=_round_time(start), end=_round_time(end))


def _removed_ranges(
    kept_ranges: list[tuple[float, float]],
    source_duration_sec: float,
) -> list[tuple[float, float]]:
    removed: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in kept_ranges:
        if start - cursor > 0.01:
            removed.append((cursor, start))
        cursor = max(cursor, end)
    if source_duration_sec - cursor > 0.01:
        removed.append((cursor, source_duration_sec))
    return removed


def _speech_ranges(
    transcript: Transcript,
    *,
    source_duration_sec: float,
    silence_gap_cut_sec: float,
    speech_lead_padding_sec: float,
    speech_padding_sec: float,
) -> list[tuple[float, float]]:
    if not transcript.words:
        end = min(source_duration_sec, max(0.1, source_duration_sec))
        return [(0.0, end)]

    words = sorted(transcript.words, key=lambda word: word.start)
    ranges: list[tuple[float, float]] = []
    range_start = max(0.0, words[0].start - speech_lead_padding_sec)
    previous = words[0]

    for word in words[1:]:
        gap = word.start - previous.end
        if gap >= silence_gap_cut_sec:
            ranges.append(
                (
                    range_start,
                    min(source_duration_sec, previous.end + speech_padding_sec),
                )
            )
            range_start = max(0.0, word.start - speech_lead_padding_sec)
        previous = word

    ranges.append(
        (
            range_start,
            min(source_duration_sec, previous.end + speech_padding_sec),
        )
    )

    merged: list[tuple[float, float]] = []
    for start, end in ranges:
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + 0.01:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged or [(0.0, min(source_duration_sec, transcript.duration))]


def _speech_ranges_duration(kept_ranges: list[tuple[float, float]]) -> float:
    return sum(end - start for start, end in kept_ranges)


def _detect_leading_silence_end(path: Path) -> float:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "silencedetect=noise=-35dB:d=0.08",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0.0

    saw_leading_silence = False
    for line in f"{result.stderr}\n{result.stdout}".splitlines():
        start_match = _SILENCE_START_RE.search(line)
        if start_match is not None:
            saw_leading_silence = float(start_match.group(1)) <= 0.05
            continue
        if not saw_leading_silence:
            continue
        end_match = _SILENCE_END_RE.search(line)
        if end_match is not None:
            return _round_time(float(end_match.group(1)))
    return 0.0


def _trim_detected_leading_silence(
    hook_path: Path,
    kept_ranges: list[tuple[float, float]],
    *,
    speech_lead_padding_sec: float,
) -> list[tuple[float, float]]:
    if not kept_ranges:
        return kept_ranges

    silence_end = _detect_leading_silence_end(hook_path)
    if silence_end <= speech_lead_padding_sec + 0.05:
        return kept_ranges

    first_start, first_end = kept_ranges[0]
    trimmed_start = max(first_start, silence_end - speech_lead_padding_sec)
    if trimmed_start <= first_start + 0.01 or trimmed_start >= first_end - 0.10:
        return kept_ranges

    return [(trimmed_start, first_end), *kept_ranges[1:]]


def _fit_speech_ranges_to_target(
    transcript: Transcript,
    *,
    source_duration_sec: float,
    silence_gap_cut_sec: float,
    speech_lead_padding_sec: float,
    speech_padding_sec: float,
    tail_padding_sec: float,
    target_max_sec: float,
) -> tuple[list[tuple[float, float]], float]:
    kept_ranges = _speech_ranges(
        transcript,
        source_duration_sec=source_duration_sec,
        silence_gap_cut_sec=silence_gap_cut_sec,
        speech_lead_padding_sec=speech_lead_padding_sec,
        speech_padding_sec=speech_padding_sec,
    )
    final_duration = _speech_ranges_duration(kept_ranges)
    if final_duration + tail_padding_sec <= target_max_sec or speech_padding_sec <= 0:
        return kept_ranges, final_duration

    for candidate_padding in (min(0.10, speech_padding_sec), 0.0):
        if candidate_padding >= speech_padding_sec:
            continue
        candidate_ranges = _speech_ranges(
            transcript,
            source_duration_sec=source_duration_sec,
            silence_gap_cut_sec=silence_gap_cut_sec,
            speech_lead_padding_sec=min(speech_lead_padding_sec, candidate_padding),
            speech_padding_sec=candidate_padding,
        )
        candidate_duration = _speech_ranges_duration(candidate_ranges)
        if candidate_duration < final_duration:
            kept_ranges = candidate_ranges
            final_duration = candidate_duration
        if final_duration + tail_padding_sec <= target_max_sec:
            break

    return kept_ranges, final_duration


def _map_time_to_montage(
    timestamp: float,
    kept_ranges: list[tuple[float, float]],
) -> float | None:
    offset = 0.0
    for start, end in kept_ranges:
        if start - 0.001 <= timestamp <= end + 0.001:
            return offset + max(0.0, timestamp - start)
        offset += end - start
    return None


def _map_word_to_montage(
    word: Word,
    kept_ranges: list[tuple[float, float]],
) -> tuple[float, float] | None:
    offset = 0.0
    for start, end in kept_ranges:
        overlap_start = max(word.start, start)
        overlap_end = min(word.end, end)
        if overlap_end > overlap_start + 0.001:
            mapped_start = offset + max(0.0, overlap_start - start)
            mapped_end = offset + max(0.0, overlap_end - start)
            return mapped_start, max(mapped_start + 0.01, mapped_end)
        offset += end - start
    return None


def _retime_transcript(
    transcript: Transcript,
    kept_ranges: list[tuple[float, float]],
) -> Transcript:
    retimed_words: list[Word] = []
    for word in transcript.words:
        mapped = _map_word_to_montage(word, kept_ranges)
        if mapped is None:
            continue
        start, end = mapped
        retimed_words.append(
            Word(
                word=word.word,
                start=_round_time(start),
                end=_round_time(max(start + 0.01, end)),
            )
        )
    return Transcript(
        words=retimed_words,
        full_text=transcript.full_text,
        duration=_round_time(retimed_words[-1].end if retimed_words else 0.0),
    )


def _accent_hit_sec(word: Word) -> float:
    duration = max(0.01, word.end - word.start)
    return min(word.end, word.start + min(0.12, duration * 0.35))


_ANCHOR_TOKEN_SPLIT_RE = re.compile(r"[^\w]+", re.UNICODE)


def _tokenize_for_anchors(text: str | None) -> list[str]:
    if not text:
        return []
    return [token for token in _ANCHOR_TOKEN_SPLIT_RE.split(text) if token]


def _hook_anchor_hits(
    transcript: Transcript,
    anchors: list[str],
) -> list[tuple[Word, int, str]]:
    hits: list[tuple[Word, int, str]] = []
    for position, word in enumerate(transcript.words):
        for anchor in anchors:
            if matches_overlay_anchor(word.word, anchor):
                hits.append((word, position, anchor))
                break
    return hits


def _cta_anchor_hit_count(cta_tokens: list[str], anchors: list[str]) -> int:
    if not cta_tokens or not anchors:
        return 0
    count = 0
    for token in cta_tokens:
        for anchor in anchors:
            if matches_overlay_anchor(token, anchor):
                count += 1
                break
    return count


def _build_cue_for_pick(
    entry: AssetEntry,
    word: Word,
    anchor: str,
    overlays_cfg: HookMontageOverlaysConfig,
) -> HookAccentCue:
    return HookAccentCue(
        keyword=anchor,
        word=word.word,
        word_start_sec=_round_time(word.start),
        word_end_sec=_round_time(word.end),
        hit_sec=_round_time(_accent_hit_sec(word)),
        file=resolve_project_path(f"assets/hook_overlays/{entry.file}"),
        placement=overlays_cfg.placement,
        width_px=overlays_cfg.width_px,
        opacity=overlays_cfg.opacity,
        enter_sec=overlays_cfg.enter_sec,
        exit_sec=overlays_cfg.exit_sec,
        display_duration_sec=overlays_cfg.display_duration_sec,
    )


def _load_hook_overlay_library() -> dict[str, AssetEntry]:
    try:
        return load_asset_meta("hook_overlays")
    except FileNotFoundError:
        return {}


def _keyword_accent_fallback(
    transcript: Transcript,
    keywords: list[str],
    overlays_cfg: HookMontageOverlaysConfig,
) -> HookAccentCue | None:
    for word in transcript.words:
        for keyword in keywords:
            if matches_anchor_root(word.word, keyword):
                return HookAccentCue(
                    keyword=keyword,
                    word=word.word,
                    word_start_sec=_round_time(word.start),
                    word_end_sec=_round_time(word.end),
                    hit_sec=_round_time(_accent_hit_sec(word)),
                    file=_TEXT_ONLY_ACCENT_FILE,
                    placement=overlays_cfg.placement,
                    width_px=overlays_cfg.width_px,
                    opacity=overlays_cfg.opacity,
                    enter_sec=overlays_cfg.enter_sec,
                    exit_sec=overlays_cfg.exit_sec,
                    display_duration_sec=overlays_cfg.display_duration_sec,
                )
    return None


def _find_accent_cues(
    transcript: Transcript,
    cfg: Config,
    *,
    cta_context_text: str | None = None,
    override_id: str | None = None,
    library: dict[str, AssetEntry] | None = None,
) -> list[HookAccentCue]:
    """Pick hook overlay icon(s) by scoring `assets/hook_overlays` against the
    hook transcript and CTA voiceover. Returns at most `max_events` cues."""

    montage = cfg.hook_montage
    overlays_cfg = montage.overlays
    if overlays_cfg.max_events <= 0:
        return []
    accent_keywords = list(montage.typography.accent_keywords)

    if library is None:
        library = _load_hook_overlay_library()
    if not library:
        fallback = _keyword_accent_fallback(transcript, accent_keywords, overlays_cfg)
        return [fallback] if fallback is not None else []

    # Explicit override: skip scoring, anchor on first matching word (fallback
    # to last word if the chosen icon's anchors are absent in the hook).
    if override_id:
        forced = library.get(override_id)
        if forced is None or not forced.anchors:
            return []
        forced_hits = _hook_anchor_hits(transcript, forced.anchors)
        if forced_hits:
            word, _, anchor = forced_hits[-1]
        elif transcript.words:
            word = transcript.words[-1]
            anchor = forced.anchors[0]
        else:
            return []
        return [_build_cue_for_pick(forced, word, anchor, overlays_cfg)]

    cta_tokens = _tokenize_for_anchors(cta_context_text)
    word_count = max(1, len(transcript.words) - 1)

    candidates: list[tuple[float, int, str, AssetEntry, Word, str]] = []
    for asset_id, entry in library.items():
        if not entry.anchors:
            continue
        hook_hits = _hook_anchor_hits(transcript, entry.anchors)
        if not hook_hits:
            continue

        cta_hits = _cta_anchor_hit_count(cta_tokens, entry.anchors)

        accent_bonus = 0.0
        if accent_keywords:
            for word, _, _ in hook_hits:
                if any(matches_anchor_root(word.word, kw) for kw in accent_keywords):
                    accent_bonus = overlays_cfg.accent_bonus
                    break

        last_word, last_position, last_anchor = hook_hits[-1]
        position_bias = (last_position / word_count) * 0.2

        score = (
            float(len(hook_hits))
            + overlays_cfg.cta_weight * cta_hits
            + accent_bonus
            + position_bias
        )

        if score < overlays_cfg.min_score:
            continue

        candidates.append((score, last_position, asset_id, entry, last_word, last_anchor))

    if not candidates:
        fallback = _keyword_accent_fallback(transcript, accent_keywords, overlays_cfg)
        return [fallback] if fallback is not None else []

    # Highest score wins; ties resolved by latest hit position (closer to accent).
    candidates.sort(key=lambda c: (-c[0], -c[1], c[2]))

    cues: list[HookAccentCue] = []
    for _, _, _, entry, word, anchor in candidates[: overlays_cfg.max_events]:
        cues.append(_build_cue_for_pick(entry, word, anchor, overlays_cfg))
    return cues


def _build_overlay_events(
    accent_cues: list[HookAccentCue],
    transcript: Transcript,
) -> list[HookOverlayEvent]:
    events: list[HookOverlayEvent] = []
    for cue in accent_cues:
        start = max(0.0, cue.hit_sec - cue.enter_sec)
        end = min(
            transcript.duration,
            max(cue.word_end_sec + cue.exit_sec, start + cue.display_duration_sec),
        )
        events.append(
            HookOverlayEvent(
                keyword=cue.keyword,
                word=cue.word,
                word_start_sec=cue.word_start_sec,
                word_end_sec=cue.word_end_sec,
                file=cue.file,
                placement=cue.placement,
                start_sec=_round_time(start),
                peak_sec=cue.hit_sec,
                end_sec=_round_time(max(start + 0.05, end)),
                width_px=cue.width_px,
                opacity=cue.opacity,
                enter_sec=cue.enter_sec,
                exit_sec=cue.exit_sec,
            )
        )
    return events


def _probe_media_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _build_sfx_events(
    accent_cues: list[HookAccentCue],
    overlays: list[HookOverlayEvent],
    final_duration_sec: float,
    cfg: Config,
) -> list[HookSfxEvent]:
    sfx = cfg.hook_montage.sfx
    if sfx.max_sfx_events <= 0 or not accent_cues:
        return []

    events: list[HookSfxEvent] = []
    primary_cue = accent_cues[0]
    primary_overlay = overlays[0] if overlays else None

    if sfx.riser:
        riser_path = resolve_project_path(sfx.riser)
        riser_duration = _probe_media_duration(riser_path)
        raw_start = primary_cue.hit_sec - sfx.riser_peak_offset_sec
        trim_start = max(0.0, -raw_start)
        timeline_start = max(0.0, raw_start)
        duration = max(0.01, riser_duration - trim_start)
        if trim_start < riser_duration:
            events.append(
                HookSfxEvent(
                    kind="riser",
                    file=riser_path,
                    timeline_start_sec=_round_time(timeline_start),
                    trim_start_sec=_round_time(trim_start),
                    duration_sec=_round_time(duration),
                    gain_db=sfx.riser_gain_db,
                    peak_target_sec=primary_cue.hit_sec,
                )
            )

    if sfx.swoosh and len(events) < sfx.max_sfx_events and primary_overlay is not None:
        swoosh_path = resolve_project_path(sfx.swoosh)
        swoosh_duration = _probe_media_duration(swoosh_path)
        candidate_start = primary_overlay.end_sec - min(0.18, max(0.0, swoosh_duration / 3))
        available_duration = max(0.0, final_duration_sec - candidate_start)
        if (
            candidate_start >= primary_cue.hit_sec + sfx.min_swoosh_gap_from_peak_sec
            and available_duration > 0.08
        ):
            events.append(
                HookSfxEvent(
                    kind="swoosh",
                    file=swoosh_path,
                    timeline_start_sec=_round_time(candidate_start),
                    trim_start_sec=0.0,
                    duration_sec=_round_time(min(swoosh_duration, available_duration)),
                    gain_db=sfx.swoosh_gain_db,
                )
            )

    return events[: sfx.max_sfx_events]


def _riser_peak_sec(sfx_events: list[HookSfxEvent]) -> float | None:
    for event in sfx_events:
        if event.kind == "riser" and event.peak_target_sec is not None:
            return event.peak_target_sec
    return None


def _build_motion_events(
    accent_cues: list[HookAccentCue],
    sfx_events: list[HookSfxEvent],
    cfg: Config,
) -> list[str]:
    motion = cfg.hook_montage.motion
    if not motion.enabled:
        return []
    events: list[str] = []
    if motion.opening_punch_in:
        events.append(
            f"opening_punch_in:{motion.opening_duration_sec:.2f}s:"
            f"{motion.opening_zoom_delta:.3f}"
        )
    if motion.keyword_punch and accent_cues:
        events.append(
            f"keyword_punch:{accent_cues[0].keyword}@{accent_cues[0].hit_sec:.2f}s:"
            f"{motion.keyword_zoom_delta:.3f}"
        )
    riser_peak = _riser_peak_sec(sfx_events)
    if motion.riser_zoom and riser_peak is not None:
        events.append(
            f"riser_zoom_out_in:@{riser_peak:.2f}s:"
            f"out={motion.riser_zoom_out_duration_sec:.2f}s:"
            f"in={motion.riser_zoom_in_duration_sec:.2f}s:"
            f"{motion.riser_zoom_delta:.3f}"
        )
    return events


def _primary_riser_event(plan: HookMontagePlan) -> HookSfxEvent | None:
    for event in plan.sfx_events:
        if event.kind == "riser" and event.peak_target_sec is not None:
            return event
    return None


def hook_effect_end_sec(diagnostics: HookMontageDiagnostics) -> float:
    if not diagnostics.enabled:
        return 0.0

    effect_end = 0.0
    for overlay in diagnostics.overlays:
        effect_end = max(effect_end, overlay.end_sec)
    for event in diagnostics.sfx_events:
        effect_end = max(effect_end, event.timeline_start_sec + event.duration_sec)
    return _round_time(effect_end)


def _plan_effect_end_sec(plan: HookMontagePlan) -> float:
    effect_end = 0.0
    for overlay in plan.overlays:
        effect_end = max(effect_end, overlay.end_sec)
    for event in plan.sfx_events:
        effect_end = max(effect_end, event.timeline_start_sec + event.duration_sec)
    return _round_time(effect_end)


def _plan_render_duration_sec(plan: HookMontagePlan, cfg: Config) -> float:
    speech_tail = plan.transcript.duration + cfg.hook_montage.tail_padding_sec
    return _round_time(max(plan.final_duration_sec, speech_tail))


def build_hook_montage_plan(
    hook_path: Path,
    transcript: Transcript,
    cfg: Config,
    *,
    cta_context_text: str | None = None,
    script_overlay_id: str | None = None,
) -> HookMontagePlan:
    source_duration = _probe_duration(hook_path)
    montage = cfg.hook_montage
    warnings: list[str] = []

    kept_ranges, final_duration = _fit_speech_ranges_to_target(
        transcript,
        source_duration_sec=source_duration,
        silence_gap_cut_sec=montage.silence_gap_cut_sec,
        speech_lead_padding_sec=montage.speech_lead_padding_sec,
        speech_padding_sec=montage.speech_padding_sec,
        tail_padding_sec=montage.tail_padding_sec,
        target_max_sec=montage.target_max_sec,
    )
    kept_ranges = _trim_detected_leading_silence(
        hook_path,
        kept_ranges,
        speech_lead_padding_sec=montage.speech_lead_padding_sec,
    )
    final_duration = _speech_ranges_duration(kept_ranges)
    retimed = _retime_transcript(transcript, kept_ranges)
    render_duration = _round_time(max(final_duration, retimed.duration + montage.tail_padding_sec))

    if render_duration < montage.target_min_sec:
        warnings.append(
            f"hook montage shorter than target_min_sec: {render_duration:.2f}s"
        )
    if render_duration > montage.target_max_sec:
        warnings.append(
            f"hook montage longer than target_max_sec: {render_duration:.2f}s"
        )

    accent_cues = _find_accent_cues(
        retimed,
        cfg,
        cta_context_text=cta_context_text,
        override_id=script_overlay_id,
    )
    overlay_cues = [cue for cue in accent_cues if cue.file != _TEXT_ONLY_ACCENT_FILE]
    overlays = _build_overlay_events(overlay_cues, retimed)
    for overlay in overlays:
        if not overlay.file.exists():
            warnings.append(f"hook overlay missing: {overlay.file}")
    sfx_events = _build_sfx_events(accent_cues, overlays, retimed.duration, cfg)
    for event in sfx_events:
        if not event.file.exists():
            warnings.append(f"hook SFX missing: {event.file}")

    return HookMontagePlan(
        source_duration_sec=source_duration,
        final_duration_sec=final_duration,
        kept_ranges=kept_ranges,
        removed_ranges=_removed_ranges(kept_ranges, source_duration),
        transcript=retimed,
        accent_cues=accent_cues,
        overlays=[overlay for overlay in overlays if overlay.file.exists()],
        sfx_events=[event for event in sfx_events if event.file.exists()],
        motion_events=_build_motion_events(accent_cues, sfx_events, cfg),
        warnings=warnings,
    )


def _token_matches_accent_cue(
    token: _SubtitleToken,
    cue: HookAccentCue,
) -> bool:
    return (
        token.start <= cue.word_end_sec + 0.001
        and token.end >= cue.word_start_sec - 0.001
        and matches_anchor_root(token.text, cue.keyword)
    )


def _split_chunks_on_accent_cues(
    chunks: list[list[_SubtitleToken]],
    accent_cues: list[HookAccentCue],
) -> list[list[_SubtitleToken]]:
    if not accent_cues:
        return chunks

    out: list[list[_SubtitleToken]] = []
    for chunk in chunks:
        pending = [chunk]
        for cue in accent_cues:
            next_pending: list[list[_SubtitleToken]] = []
            for part in pending:
                accent_index = next(
                    (index for index, token in enumerate(part) if _token_matches_accent_cue(token, cue)),
                    None,
                )
                if accent_index is None or len(part) == 1:
                    next_pending.append(part)
                    continue
                if accent_index > 0:
                    next_pending.append(part[:accent_index])
                next_pending.append([part[accent_index]])
                if accent_index + 1 < len(part):
                    next_pending.append(part[accent_index + 1:])
            pending = next_pending
        out.extend(part for part in pending if part)
    return out


def _accent_cue_for_chunk(
    chunk: list[_SubtitleToken],
    accent_cues: list[HookAccentCue],
    cfg: Config,
) -> HookAccentCue | None:
    for cue in accent_cues:
        if any(_token_matches_accent_cue(token, cue) for token in chunk):
            return cue

    keywords = [
        *cfg.hook_montage.typography.accent_keywords,
    ]
    if not keywords:
        return None
    if any(
        matches_anchor_root(word, keyword)
        for word in (token.text for token in chunk)
        for keyword in keywords
    ):
        first = chunk[0]
        return HookAccentCue(
            keyword=keywords[0],
            word=first.text,
            word_start_sec=_round_time(first.start),
            word_end_sec=_round_time(first.end),
            hit_sec=_round_time(_accent_hit_sec(Word(word=first.text, start=first.start, end=first.end))),
            file=Path(),
            placement="right_mid",
            width_px=0,
            opacity=1.0,
            enter_sec=0.0,
            exit_sec=0.0,
            display_duration_sec=0.0,
        )
    return None


def write_hook_montage_ass(
    transcript: Transcript,
    style: SubtitleStyle,
    out_path: Path,
    *,
    cfg: Config,
    accent_cues: list[HookAccentCue],
    layout_warnings: list[str] | None = None,
) -> Path:
    if style.caption_mode == "editorial":
        return transcript_to_ass(
            transcript,
            style,
            out_path,
            safe_box=cfg.subtitle_safe_box,
            cfg=cfg,
            layout_warnings=layout_warnings,
            section="hook",
        )

    header = _ass_header(style, safe_box=cfg.subtitle_safe_box, title="Hook Montage")
    events: list[str] = []
    warnings: list[str] = []

    tokens = _subtitle_tokens(transcript, style)
    chunks = _chunk_tokens(tokens, style)
    chunks = _split_chunks_on_accent_cues(chunks, accent_cues)
    intervals = _chunk_intervals(chunks, style)
    center_x = cfg.subtitle_safe_box.center_x
    typography = cfg.hook_montage.typography

    for index, chunk in enumerate(chunks):
        accent_cue = _accent_cue_for_chunk(chunk, accent_cues, cfg)
        chunk_words = [token.text for token in chunk]
        accent = accent_cue is not None
        active_style = (
            style.model_copy(update={"size": typography.accent_size})
            if accent
            else style
        )
        layout = _layout_chunk(
            chunk_words,
            style=active_style,
            safe_box=cfg.subtitle_safe_box,
            cfg=cfg,
        )
        if layout.warning is not None:
            warnings.append(layout.warning)

        y = typography.accent_y_px if accent else typography.base_y_px
        prefix = f"{{\\an8\\pos({center_x},{y})}}"
        if style.fade_in_ms or style.fade_out_ms:
            prefix = f"{prefix}{{\\fad({style.fade_in_ms},{style.fade_out_ms})}}"
        if accent:
            prefix = f"{prefix}{{\\fs{layout.base_size_override or typography.accent_size}}}"
        elif layout.base_size_override is not None:
            prefix = f"{prefix}{{\\fs{layout.base_size_override}}}"

        events.append(
            _dialogue_line(
                intervals[index][0],
                intervals[index][1],
                prefix,
                layout.display_text,
            )
        )

    if layout_warnings is not None:
        layout_warnings.extend(warnings)
    for warning in warnings:
        console.log(f"[yellow]![/yellow] {warning}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    console.log(f"[green]✓[/green] Hook montage subtitles saved: {out_path}")
    return out_path


def _overlay_position(placement: HookOverlayPlacement) -> tuple[str, str]:
    if placement == "left_mid":
        return "88", "main_h*0.38-overlay_h/2"
    if placement == "top_right":
        return "main_w-overlay_w-76", "300"
    if placement == "top_left":
        return "76", "300"
    if placement == "bottom_right":
        return "main_w-overlay_w-76", "main_h-overlay_h-300"
    if placement == "bottom_left":
        return "76", "main_h-overlay_h-300"
    return "main_w-overlay_w-88", "main_h*0.38-overlay_h/2"


def _smootherstep_expr(progress: str) -> str:
    return f"(({progress})*({progress})*({progress})*(({progress})*(6*({progress})-15)+10))"


def _hook_motion_filter(
    *,
    width: int,
    height: int,
    plan: HookMontagePlan,
    cfg: Config,
) -> str | None:
    motion = cfg.hook_montage.motion
    if not motion.enabled:
        return None

    components: list[str] = []
    time_expr = "it"
    riser_event = _primary_riser_event(plan)
    if motion.riser_zoom and riser_event is not None:
        peak = riser_event.peak_target_sec or 0.0
        zoom_out_duration = min(
            motion.riser_zoom_out_duration_sec,
            max(0.001, peak),
        )
        zoom_out_start = max(0.0, peak - zoom_out_duration)
        zoom_in_duration = motion.riser_zoom_in_duration_sec
        out_progress = (
            f"min(max(({time_expr}-{zoom_out_start:.3f})/{zoom_out_duration:.3f},0),1)"
        )
        out_smooth = _smootherstep_expr(out_progress)
        in_progress = f"min(max(({time_expr}-{peak:.3f})/{zoom_in_duration:.3f},0),1)"
        in_smooth = _smootherstep_expr(in_progress)
        post_peak = f"gte({time_expr},{peak:.3f})"
        components.append(
            f"({motion.riser_zoom_delta:.5f}*((1-({post_peak}))*(1-({out_smooth}))+"
            f"({post_peak})*({in_smooth})))"
        )
    if motion.opening_punch_in:
        components.append(
            f"{motion.opening_zoom_delta:.5f}*"
            f"min(max({time_expr}/{motion.opening_duration_sec:.3f},0),1)"
        )
    if motion.keyword_punch and plan.accent_cues:
        peak = plan.accent_cues[0].hit_sec
        window = motion.keyword_punch_duration_sec
        components.append(
            f"{motion.keyword_zoom_delta:.5f}*max(0,1-abs({time_expr}-{peak:.3f})/{window:.3f})"
        )

    if not components:
        return None

    zoom_expr = "1+" + "+".join(components)
    work_width = width * 2
    work_height = height * 2
    return ",".join(
        [
            f"fps={cfg.video.fps}",
            f"scale={work_width}:{work_height}:flags=lanczos",
            (
                f"zoompan=z='{zoom_expr}':"
                "x='iw/2-(iw/zoom/2)':"
                "y='ih/2-(ih/zoom/2)':"
                f"d=1:fps={cfg.video.fps}:s={work_width}x{work_height}"
            ),
            f"scale={width}:{height}:flags=lanczos",
            "setsar=1",
        ]
    )


def _hook_video_filters(
    hook_path: Path,
    plan: HookMontagePlan,
    cfg: Config,
) -> tuple[list[str], str]:
    w, h = cfg.video.resolution.split("x")
    width = int(w)
    height = int(h)
    chains: list[str] = []
    concat_inputs: list[str] = []

    for index, (start, end) in enumerate(plan.kept_ranges):
        chains.append(
            "".join(
                [
                    f"[0:v]trim=start={start:.3f}:end={end:.3f},",
                    "setpts=PTS-STARTPTS,",
                    _video_normalize_chain(hook_path, w, h),
                    f",pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1[vhook{index}]",
                ]
            )
        )
        chains.append(
            f"[0:a]atrim=start={start:.3f}:end={end:.3f},"
            f"asetpts=PTS-STARTPTS[ahook{index}]"
        )
        concat_inputs.append(f"[vhook{index}][ahook{index}]")

    chains.append(
        f"{''.join(concat_inputs)}concat=n={len(plan.kept_ranges)}:v=1:a=1[vtrim][atrim]"
    )

    current_label = "vtrim"
    render_duration = _plan_render_duration_sec(plan, cfg)
    extra_duration = max(0.0, render_duration - plan.final_duration_sec)
    if extra_duration > 0:
        chains.append(
            f"[{current_label}]tpad=stop_mode=clone:stop_duration={extra_duration:.3f},"
            f"trim=duration={render_duration:.3f},setpts=PTS-STARTPTS[vpad]"
        )
        current_label = "vpad"

    motion_filter = _hook_motion_filter(width=width, height=height, plan=plan, cfg=cfg)
    if motion_filter is not None:
        chains.append(f"[{current_label}]{motion_filter}[vmotion]")
        current_label = "vmotion"

    for index, overlay in enumerate(plan.overlays):
        image_input_index = 1 + index
        icon_label = f"icon{index}"
        out_label = f"voverlay{index}"
        x_expr, y_expr = _overlay_position(overlay.placement)
        fade_out_start = max(overlay.start_sec, overlay.end_sec - overlay.exit_sec)
        chains.append(
            f"[{image_input_index}:v]scale={overlay.width_px}:-1,format=rgba,"
            f"colorchannelmixer=aa={overlay.opacity:.3f},"
            f"fade=t=in:st={overlay.start_sec:.3f}:d={overlay.enter_sec:.3f}:alpha=1,"
            f"fade=t=out:st={fade_out_start:.3f}:d={overlay.exit_sec:.3f}:alpha=1"
            f"[{icon_label}]"
        )
        chains.append(
            f"[{current_label}][{icon_label}]overlay=x={x_expr}:y={y_expr}:"
            f"enable='between(t,{overlay.start_sec:.3f},{overlay.end_sec:.3f})'"
            f"[{out_label}]"
        )
        current_label = out_label

    return chains, current_label


def _hook_audio_filters(
    plan: HookMontagePlan,
    cfg: Config,
    *,
    first_sfx_input_index: int,
) -> tuple[list[str], str]:
    render_duration = _plan_render_duration_sec(plan, cfg)
    chains = [
        f"[atrim]apad=pad_dur={max(0.0, render_duration - plan.final_duration_sec):.3f},"
        f"atrim=duration={render_duration:.3f},asetpts=PTS-STARTPTS,"
        f"{_spoken_audio_filter(cfg, gain_db=0.0)}[hookvoice]"
    ]
    mix_inputs = ["[hookvoice]"]

    for index, event in enumerate(plan.sfx_events):
        input_index = first_sfx_input_index + index
        label = f"hooksfx{index}"
        delay_ms = max(0, round(event.timeline_start_sec * 1000))
        chains.append(
            f"[{input_index}:a]atrim=start={event.trim_start_sec:.3f}:"
            f"duration={event.duration_sec:.3f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates={cfg.audio.sample_rate}:channel_layouts=stereo,"
            f"volume={event.gain_db:.2f}dB,"
            f"adelay={delay_ms}|{delay_ms}[{label}]"
        )
        mix_inputs.append(f"[{label}]")

    if len(mix_inputs) == 1:
        return chains, "hookvoice"

    chains.append(
        f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:"
        f"duration=first:dropout_transition=0:normalize=0[hookaudio]"
    )
    return chains, "hookaudio"


def _diagnostics(plan: HookMontagePlan, cfg: Config) -> HookMontageDiagnostics:
    return HookMontageDiagnostics(
        enabled=True,
        source_duration_sec=_round_time(plan.source_duration_sec),
        speech_duration_sec=_round_time(plan.final_duration_sec),
        tail_padding_sec=_round_time(cfg.hook_montage.tail_padding_sec),
        final_duration_sec=_plan_render_duration_sec(plan, cfg),
        effect_end_sec=_plan_effect_end_sec(plan),
        kept_ranges=[_time_range(start, end) for start, end in plan.kept_ranges],
        removed_ranges=[_time_range(start, end) for start, end in plan.removed_ranges],
        overlays=[
            HookMontageOverlayDiagnostics(
                keyword=overlay.keyword,
                word=overlay.word,
                file=str(overlay.file),
                placement=overlay.placement,
                word_start_sec=overlay.word_start_sec,
                word_end_sec=overlay.word_end_sec,
                start_sec=overlay.start_sec,
                peak_sec=overlay.peak_sec,
                end_sec=overlay.end_sec,
                width_px=overlay.width_px,
                opacity=overlay.opacity,
            )
            for overlay in plan.overlays
        ],
        sfx_events=[
            HookMontageSfxDiagnostics(
                kind=event.kind,
                file=str(event.file),
                timeline_start_sec=event.timeline_start_sec,
                trim_start_sec=event.trim_start_sec,
                duration_sec=event.duration_sec,
                gain_db=event.gain_db,
                peak_target_sec=event.peak_target_sec,
            )
            for event in plan.sfx_events
        ],
        motion_events=plan.motion_events,
        warnings=plan.warnings,
    )


def render_hook_montage(
    hook_path: Path,
    transcript: Transcript,
    style: SubtitleStyle,
    out_dir: Path,
    cfg: Config,
    *,
    cta_context_text: str | None = None,
    script_overlay_id: str | None = None,
) -> HookMontageResult:
    if not cfg.hook_montage.enabled:
        subtitle_path = out_dir / "hook_subs.ass"
        subtitle_warnings: list[str] = []
        write_hook_montage_ass(
            transcript,
            style,
            subtitle_path,
            cfg=cfg,
            accent_cues=[],
            layout_warnings=subtitle_warnings,
        )
        return HookMontageResult(
            video_path=hook_path,
            subtitle_path=subtitle_path,
            transcript=transcript,
            diagnostics=HookMontageDiagnostics(enabled=False),
            subtitle_warnings=subtitle_warnings,
        )

    plan = build_hook_montage_plan(
        hook_path,
        transcript,
        cfg,
        cta_context_text=cta_context_text,
        script_overlay_id=script_overlay_id,
    )
    subtitle_path = out_dir / "hook_montage_subs.ass"
    subtitle_warnings: list[str] = []
    write_hook_montage_ass(
        plan.transcript,
        style,
        subtitle_path,
        cfg=cfg,
        accent_cues=plan.accent_cues,
        layout_warnings=subtitle_warnings,
    )

    out_path = out_dir / "hook_montage.mp4"
    video_chains, video_label = _hook_video_filters(hook_path, plan, cfg)
    first_sfx_input_index = 1 + len(plan.overlays)
    audio_chains, audio_label = _hook_audio_filters(
        plan,
        cfg,
        first_sfx_input_index=first_sfx_input_index,
    )

    cmd = ["ffmpeg", "-y", "-i", str(hook_path)]
    render_duration = _plan_render_duration_sec(plan, cfg)
    for overlay in plan.overlays:
        cmd.extend(["-loop", "1", "-t", f"{render_duration:.3f}", "-i", str(overlay.file)])
    for event in plan.sfx_events:
        cmd.extend(["-i", str(event.file)])
    cmd.extend(
        [
            "-filter_complex",
            ";".join([*video_chains, *audio_chains]),
            "-map",
            f"[{video_label}]",
            "-map",
            f"[{audio_label}]",
            "-r",
            str(cfg.video.fps),
            "-c:v",
            cfg.video.codec,
            "-crf",
            str(cfg.video.crf_intermediate),
            "-x264-params",
            _X264_COLOR_PARAMS,
            *_COLOR_OUT_TAGS,
            "-c:a",
            "aac",
            "-ar",
            str(cfg.audio.sample_rate),
            "-ac",
            "2",
            "-shortest",
            str(out_path),
        ]
    )
    _run(cmd, "hook montage")
    diagnostics = _diagnostics(plan, cfg)
    diagnostics_path = out_dir / "hook_montage_plan.json"
    diagnostics_path.write_text(
        diagnostics.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )
    console.log(f"[green]✓[/green] Hook montage plan saved: {diagnostics_path}")

    return HookMontageResult(
        video_path=out_path,
        subtitle_path=subtitle_path,
        transcript=plan.transcript,
        diagnostics=diagnostics,
        subtitle_warnings=subtitle_warnings,
    )
