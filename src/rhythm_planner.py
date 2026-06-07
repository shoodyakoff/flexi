from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from .assets import (
    load_broll_recent_history,
    load_usage_counts,
    resolve_project_path,
    save_broll_recent_history,
    save_usage_counts,
)
from .broll_picker import (
    _ffprobe_duration,
    build_broll_clip,
    choose_broll_candidate,
    library_supports_full_rules,
    resolve_broll_pool,
)
from .schemas import (
    AssetEntry,
    AssetSnapshot,
    BRollClip,
    BlockType,
    BrollStrategy,
    Config,
    MotionPreset,
    MotionProfileConfig,
    ProductInsert,
    ProductInsertRules,
    RenderPlan,
    RenderPlanClip,
    RenderPlanTransition,
    RhythmRole,
    RhythmRoleBudget,
    Transcript,
)
from .text_matching import matches_anchor_root, normalize_word_token

console = Console()

_SHORT_CLIP_SEC = 1.1
_MAX_SHORT_CLIP_STREAK = 2
_REST_ANCHOR_MIN_DURATION_SEC = 2.4
_REST_ANCHOR_MAX_GAP_SEC = 6.5
_REST_ANCHOR_FORCE_AFTER_SEC = 5.5


@dataclass(frozen=True)
class _TimelineClip:
    clip: BRollClip
    entry: AssetEntry | None
    rhythm_role: RhythmRole | None
    placement_reason: str
    block_kind: BlockType | None = None
    block_reason: str | None = None
    is_product_insert: bool = False


def _clip_duration(clip: BRollClip) -> float:
    return max(0.0, clip.end - clip.start)


def _asset_snapshot(entry: AssetEntry | None) -> AssetSnapshot | None:
    if entry is None:
        return None
    return AssetSnapshot(
        duration=entry.duration,
        scene_type=entry.scene_type,
        scene_group=entry.scene_group,
        shot_scale=entry.shot_scale,
        subject_kind=entry.subject_kind,
        sequence_role=entry.sequence_role,
        energy=entry.energy,
        motion_allowed=entry.motion_allowed,
        allowed_motion_presets=entry.allowed_motion_presets,
        motion_intensity_cap=entry.motion_intensity_cap,
        effect_profile_override=entry.effect_profile_override,
        weight=entry.weight,
        trim_policy=entry.trim_policy,
    )


def _active_rhythm(cfg: Config):
    profile_name = cfg.edit_profile.rhythm_profile
    try:
        return cfg.rhythm_profiles[profile_name]
    except KeyError as exc:
        raise ValueError(f"rhythm_profile '{profile_name}' not configured") from exc


def _trim_item(item: _TimelineClip, take: float) -> _TimelineClip:
    take = max(0.0, min(take, _clip_duration(item.clip)))
    return _TimelineClip(
        clip=BRollClip(
            asset_id=item.clip.asset_id,
            file=item.clip.file,
            start=item.clip.start,
            end=item.clip.start + take,
        ),
        entry=item.entry,
        rhythm_role=item.rhythm_role,
        placement_reason=item.placement_reason,
        block_kind=item.block_kind,
        block_reason=item.block_reason,
        is_product_insert=item.is_product_insert,
    )


def _is_keep_full(item: _TimelineClip) -> bool:
    return item.entry is not None and item.entry.trim_policy == "keep_full"


def _is_detail_visual(item: _TimelineClip) -> bool:
    if item.rhythm_role == "detail":
        return True
    if item.entry is None:
        return False
    return item.entry.shot_scale in {"detail", "close"} or item.entry.sequence_role == "detail"


def _extend_item(item: _TimelineClip, extra: float) -> tuple[_TimelineClip, float]:
    if extra <= 0:
        return item, 0.0

    source_limit = item.entry.duration if item.entry is not None else item.clip.end
    available = max(0.0, source_limit - item.clip.end)
    extension = min(extra, available)
    if extension <= 0:
        return item, extra

    return (
        _TimelineClip(
            clip=BRollClip(
                asset_id=item.clip.asset_id,
                file=item.clip.file,
                start=item.clip.start,
                end=item.clip.end + extension,
            ),
            entry=item.entry,
            rhythm_role=item.rhythm_role,
            placement_reason=f"{item.placement_reason}; absorbed short tail",
            block_kind=item.block_kind,
            block_reason=item.block_reason,
            is_product_insert=item.is_product_insert,
        ),
        extra - extension,
    )


def _slice_item(item: _TimelineClip, source_start: float, source_end: float) -> _TimelineClip:
    return _TimelineClip(
        clip=BRollClip(
            asset_id=item.clip.asset_id,
            file=item.clip.file,
            start=source_start,
            end=source_end,
        ),
        entry=item.entry,
        rhythm_role=item.rhythm_role,
        placement_reason=item.placement_reason,
        block_kind=item.block_kind,
        block_reason=item.block_reason,
        is_product_insert=item.is_product_insert,
    )


def _budget_duration(
    budget: RhythmRoleBudget,
    remaining: float,
    *,
    role: RhythmRole,
    step_index: int,
) -> float:
    if remaining <= budget.max_duration_sec:
        return max(0.25, remaining)

    if role == "detail":
        preferred = budget.preferred_duration_min_sec
    elif role == "anchor":
        preferred = budget.preferred_duration_max_sec
    else:
        span = budget.preferred_duration_max_sec - budget.preferred_duration_min_sec
        ratios = [0.35, 0.65, 0.15, 0.85]
        preferred = budget.preferred_duration_min_sec + span * ratios[step_index % len(ratios)]

    return max(budget.min_duration_sec, min(preferred, budget.max_duration_sec, remaining))


def _collect_breakpoints(transcript: Transcript | None, phrase_gap_min_sec: float) -> list[float]:
    if transcript is None or len(transcript.words) < 2:
        return []

    breakpoints: set[float] = set()
    for word, next_word in zip(transcript.words, transcript.words[1:]):
        gap = next_word.start - word.end
        if gap >= phrase_gap_min_sec:
            breakpoints.add(word.end)
        if re.search(r"[.!?…。;:]$", word.word.strip()):
            breakpoints.add(word.end)
    return sorted(point for point in breakpoints if point > 0)


def _snap_duration_to_breakpoint(
    accumulated: float,
    planned_duration: float,
    remaining: float,
    budget: RhythmRoleBudget,
    breakpoints: list[float],
    tolerance_sec: float,
) -> tuple[float, str]:
    desired_end = accumulated + planned_duration
    candidates = [
        point
        for point in breakpoints
        if accumulated < point < accumulated + remaining
        and abs(point - desired_end) <= tolerance_sec
    ]
    for point in sorted(candidates, key=lambda value: abs(value - desired_end)):
        snapped_duration = point - accumulated
        if snapped_duration <= 0.25:
            continue
        if snapped_duration > budget.max_duration_sec:
            continue
        if snapped_duration < budget.min_duration_sec and remaining > budget.max_duration_sec:
            continue
        return snapped_duration, f"snapped to speech breakpoint at {point:.2f}s"
    return planned_duration, "role duration budget"


def _desired_scales(budget: RhythmRoleBudget) -> tuple[str, ...]:
    return tuple(budget.shot_scales) if budget.shot_scales else ("medium", "wide")


def _desired_sequence_role(budget: RhythmRoleBudget) -> str:
    return budget.sequence_roles[0] if budget.sequence_roles else "action"


def _planned_block_kind(
    pattern: list[RhythmRole],
    *,
    role: RhythmRole,
    step_index: int,
) -> tuple[BlockType, str]:
    cycle_index = step_index % len(pattern)

    if role == "detail":
        return "demo", "detail beat maps to demo block"
    if role == "anchor":
        return "turn", "anchor beat maps to turn block"
    if cycle_index == 0:
        return "explain", "cycle opens with explain block"
    if cycle_index == len(pattern) - 2:
        return "transition", "pre-anchor support becomes transition block"
    return "explain", "support beat remains explain block"


def _block_scales(
    budget: RhythmRoleBudget,
    block_kind: BlockType,
) -> tuple[str, ...]:
    if block_kind == "demo":
        return ("detail", "close", "medium")
    if block_kind == "transition":
        return ("wide", "medium")
    if block_kind == "turn":
        return ("medium", "wide")
    return _desired_scales(budget)


def _block_sequence_role(
    budget: RhythmRoleBudget,
    block_kind: BlockType,
) -> str:
    if block_kind == "demo":
        return "detail"
    if block_kind == "transition":
        return "transition"
    if block_kind == "turn":
        return "action"
    return _desired_sequence_role(budget)


def _plan_manual_sequence(
    pool: list[tuple[str, AssetEntry]],
    target_duration: float,
    pattern: list[RhythmRole],
) -> list[_TimelineClip]:
    clips: list[_TimelineClip] = []
    accumulated = 0.0
    use_counts: Counter[str] = Counter()
    index = 0
    while accumulated < target_duration - 0.05:
        asset_id, entry = pool[index % len(pool)]
        remaining = target_duration - accumulated
        take = min(entry.duration, remaining)
        role = pattern[len(clips) % len(pattern)]
        block_kind, block_reason = _planned_block_kind(
            pattern,
            role=role,
            step_index=len(clips),
        )
        clip = build_broll_clip(asset_id, entry, take, use_index=use_counts[asset_id])
        use_counts[asset_id] += 1
        clips.append(
            _TimelineClip(
                clip=clip,
                entry=entry,
                rhythm_role=role,
                placement_reason="manual override order",
                block_kind=block_kind,
                block_reason=block_reason,
            )
        )
        accumulated += _clip_duration(clip)
        index += 1
    return clips


def _plan_role_sequence(
    pool: list[tuple[str, AssetEntry]],
    *,
    target_duration: float,
    cfg: Config,
    transcript: Transcript | None,
    soft_mode: bool,
    global_use_counts: dict[str, int] | None = None,
    recent_start_assets: set[str] | None = None,
    recent_anywhere_assets: set[str] | None = None,
    recent_start_penalty: float = 0.0,
    recent_anywhere_penalty: float = 0.0,
) -> list[_TimelineClip]:
    rhythm = _active_rhythm(cfg)
    pattern = rhythm.pattern
    breakpoints = _collect_breakpoints(transcript, rhythm.phrase_gap_min_sec)

    clips: list[_TimelineClip] = []
    accumulated = 0.0
    history: list[tuple[str, AssetEntry]] = []
    use_counts: Counter[str] = Counter()
    short_clip_streak = 0
    last_rest_anchor_start = 0.0

    while accumulated < target_duration - 0.05:
        remaining = target_duration - accumulated
        pattern_role = pattern[len(clips) % len(pattern)]
        role = pattern_role
        role_reason: str | None = None
        time_since_rest = accumulated - last_rest_anchor_start
        if accumulated > 0 and time_since_rest >= _REST_ANCHOR_FORCE_AFTER_SEC:
            role = "anchor"
            role_reason = (
                f"forced rest anchor after {time_since_rest:.1f}s without a visual rest"
            )
        elif short_clip_streak >= _MAX_SHORT_CLIP_STREAK:
            role = "anchor"
            role_reason = (
                f"forced rest anchor after {short_clip_streak} short clips"
            )

        budget = rhythm.roles[role]
        block_kind, block_reason = _planned_block_kind(
            pattern,
            role=role,
            step_index=len(clips),
        )
        planned_duration = _budget_duration(
            budget,
            remaining,
            role=role,
            step_index=len(clips),
        )
        planned_duration, placement_reason = _snap_duration_to_breakpoint(
            accumulated,
            planned_duration,
            remaining,
            budget,
            breakpoints,
            rhythm.snap_tolerance_sec,
        )
        if role_reason is not None:
            placement_reason = f"{role_reason}; {placement_reason}"

        minimum_take_sec: float | None = None
        if short_clip_streak >= _MAX_SHORT_CLIP_STREAK:
            minimum_take_sec = _SHORT_CLIP_SEC
        if role == "anchor" and remaining >= _REST_ANCHOR_MIN_DURATION_SEC:
            minimum_take_sec = max(minimum_take_sec or 0.0, _REST_ANCHOR_MIN_DURATION_SEC)

        asset_id, entry, take = choose_broll_candidate(
            pool,
            desired_block=block_kind,
            desired_scales=_block_scales(budget, block_kind),
            desired_role=_block_sequence_role(budget, block_kind),
            history=history,
            soft_mode=soft_mode,
            planned_duration_sec=planned_duration,
            remaining_duration_sec=remaining,
            use_counts=use_counts,
            minimum_take_sec=minimum_take_sec,
            global_use_counts=global_use_counts,
            recent_start_assets=recent_start_assets,
            recent_anywhere_assets=recent_anywhere_assets,
            recent_start_penalty=recent_start_penalty,
            recent_anywhere_penalty=recent_anywhere_penalty,
        )
        clip = build_broll_clip(asset_id, entry, take, use_index=use_counts[asset_id])
        use_counts[asset_id] += 1
        clips.append(
            _TimelineClip(
                clip=clip,
                entry=entry,
                rhythm_role=role,
                placement_reason=placement_reason,
                block_kind=block_kind,
                block_reason=block_reason,
            )
        )
        actual_duration = _clip_duration(clip)
        accumulated += actual_duration
        history.append((asset_id, entry))
        if actual_duration < _SHORT_CLIP_SEC:
            short_clip_streak += 1
        else:
            short_clip_streak = 0
        if role == "anchor" and actual_duration >= _REST_ANCHOR_MIN_DURATION_SEC:
            last_rest_anchor_start = accumulated - actual_duration

    return clips


def _strip_punct(word: str) -> str:
    return normalize_word_token(word)


def _marker_time(
    transcript: Transcript,
    *,
    product_break_after_token: int | None,
    warnings: list[str],
) -> float | None:
    if product_break_after_token is None:
        return None
    if not transcript.words:
        warnings.append("product_break marker ignored: transcript has no words")
        return None
    if product_break_after_token <= 0:
        warnings.append("product_break marker ignored: marker is before the first aligned token")
        return None
    if product_break_after_token >= len(transcript.words):
        warnings.append("product_break marker ignored: marker is at or after transcript end")
        return None
    return transcript.words[product_break_after_token - 1].end


def _anchor_time(transcript: Transcript, overlay: ProductInsert) -> tuple[float | None, str | None]:
    if not overlay.anchor_root:
        return None, None

    for word in transcript.words:
        if matches_anchor_root(word.word, overlay.anchor_root):
            return max(0.0, word.start - overlay.lead_in_sec), f"anchor '{word.word}'"
    return None, None


def _pause_fallback_time(transcript: Transcript, rules: ProductInsertRules) -> float | None:
    if len(transcript.words) < 2:
        return None

    min_start = transcript.duration * rules.fallback_pause_after_ratio
    for previous_word, next_word in zip(transcript.words, transcript.words[1:]):
        gap = next_word.start - previous_word.end
        if previous_word.end >= min_start and gap >= rules.fallback_pause_min_gap_sec:
            return previous_word.end
    return None


def _resolve_product_target_time(
    clips: list[_TimelineClip],
    transcript: Transcript,
    overlay: ProductInsert,
    rules: ProductInsertRules,
    *,
    product_break_after_token: int | None,
    warnings: list[str],
) -> tuple[float, str]:
    marker = _marker_time(
        transcript,
        product_break_after_token=product_break_after_token,
        warnings=warnings,
    )
    if marker is not None:
        return marker, "marker-driven break"

    anchor, reason = _anchor_time(transcript, overlay)
    if anchor is not None and reason is not None:
        return anchor, reason

    pause = _pause_fallback_time(transcript, rules)
    if pause is not None:
        return pause, "alignment pause fallback"

    timeline_total = sum(_clip_duration(item.clip) for item in clips)
    return timeline_total * rules.timeline_fallback_ratio, "timeline fallback"


def _product_duration(
    product_path: Path,
    available_sec: float,
    rules: ProductInsertRules,
    warnings: list[str],
) -> float:
    full_duration = _ffprobe_duration(product_path)
    max_duration = min(full_duration, available_sec, rules.max_duration_sec)
    if max_duration <= 0:
        return 0.0

    duration = min(max_duration, rules.preferred_duration_max_sec)
    if duration < rules.min_duration_sec and max_duration >= rules.min_duration_sec:
        duration = min(max_duration, rules.min_duration_sec)
    if duration < rules.min_duration_sec:
        warnings.append(
            "product insert shorter than configured minimum because timeline has too little room"
        )
    return max(0.0, duration)


def _insert_product_clip(
    clips: list[_TimelineClip],
    transcript: Transcript | None,
    overlay: ProductInsert | None,
    rules: ProductInsertRules,
    *,
    product_break_after_token: int | None,
    warnings: list[str],
) -> tuple[list[_TimelineClip], str | None]:
    if overlay is None:
        warnings.append("product_insert is not configured; b-roll plan has no product insert")
        return clips, None
    if transcript is None:
        warnings.append("product_insert skipped because transcript is missing")
        return clips, None

    product_path = resolve_project_path(overlay.file)
    if not product_path.exists():
        raise ValueError(f"product_insert file not found: {product_path}")

    target_total = sum(_clip_duration(item.clip) for item in clips)
    target_time, target_reason = _resolve_product_target_time(
        clips,
        transcript,
        overlay,
        rules,
        product_break_after_token=product_break_after_token,
        warnings=warnings,
    )
    target_time = max(0.0, min(target_time, target_total))

    timeline = 0.0
    target_idx: int | None = None
    target_clip_start = 0.0
    for idx, item in enumerate(clips):
        duration = _clip_duration(item.clip)
        if timeline + duration > target_time:
            target_idx = idx
            target_clip_start = timeline
            break
        timeline += duration

    if target_idx is None:
        snapped_time = target_total
        before = clips
        after: list[_TimelineClip] = []
        decision = "append-at-end"
    else:
        item = clips[target_idx]
        offset = target_time - target_clip_start
        duration = _clip_duration(item.clip)
        snap = overlay.snap_window_sec
        min_split_fragment_sec = max(0.8, snap)

        if offset <= snap:
            snapped_time = target_clip_start
            before = clips[:target_idx]
            after = clips[target_idx:]
            decision = f"snap-early ({offset:.2f}s before clip boundary)"
        elif duration - offset <= snap:
            snapped_time = target_clip_start + duration
            before = clips[: target_idx + 1]
            after = clips[target_idx + 1 :]
            decision = f"snap-late ({duration - offset:.2f}s before clip end)"
        elif _is_keep_full(item):
            if offset <= duration / 2:
                snapped_time = target_clip_start
                before = clips[:target_idx]
                after = clips[target_idx:]
                decision = "snap-early (protected keep_full clip)"
            else:
                snapped_time = target_clip_start + duration
                before = clips[: target_idx + 1]
                after = clips[target_idx + 1 :]
                decision = "snap-late (protected keep_full clip)"
        elif offset < min_split_fragment_sec:
            snapped_time = target_clip_start
            before = clips[:target_idx]
            after = clips[target_idx:]
            decision = f"snap-early (short {offset:.2f}s leading fragment)"
        elif duration - offset < min_split_fragment_sec:
            snapped_time = target_clip_start + duration
            before = clips[: target_idx + 1]
            after = clips[target_idx + 1 :]
            decision = f"snap-late (short {duration - offset:.2f}s trailing fragment)"
        else:
            split_at = item.clip.start + offset
            before_piece = _slice_item(item, item.clip.start, split_at)
            after_piece = _slice_item(item, split_at, item.clip.end)
            before = clips[:target_idx] + [before_piece]
            after = [after_piece] + clips[target_idx + 1 :]
            snapped_time = target_time
            decision = f"hard-cut {offset:.2f}s into clip"

    available_for_product = target_total - snapped_time
    product_take = _product_duration(product_path, available_for_product, rules, warnings)
    if product_take <= 0.25:
        warnings.append("product insert skipped because there is no usable room in timeline")
        return clips, None

    product_item = _TimelineClip(
        clip=BRollClip(
            asset_id="product_insert",
            file=str(product_path),
            start=0.0,
            end=product_take,
        ),
        entry=None,
        rhythm_role="anchor",
        placement_reason=f"{target_reason}; {decision}",
        block_kind="demo",
        block_reason="product insert is treated as a demo block",
        is_product_insert=True,
    )

    dropped_before_sec = 0.0
    if before and _is_detail_visual(before[-1]) and not _is_keep_full(before[-1]):
        dropped = before.pop()
        dropped_before_sec = _clip_duration(dropped.clip)
        decision = f"{decision}; dropped detail neighbor before product insert"
        warnings.append(
            "product insert dropped adjacent detail clip before insert to keep the reveal calmer"
        )
        snapped_time = max(0.0, snapped_time - dropped_before_sec)

    new_clips = before + [product_item]
    remaining = target_total - (snapped_time + product_take)
    min_tail_sec = 0.8
    skipped_after_detail = False
    for item in after:
        if remaining <= 0.05:
            break
        if (
            not skipped_after_detail
            and _is_detail_visual(item)
            and not _is_keep_full(item)
        ):
            skipped_after_detail = True
            decision = f"{decision}; skipped detail neighbor after product insert"
            warnings.append(
                "product insert skipped adjacent detail clip after insert to keep the reveal calmer"
            )
            continue
        skipped_after_detail = True
        take = min(_clip_duration(item.clip), remaining)
        if _is_keep_full(item) and take < _clip_duration(item.clip):
            continue
        if take < min_tail_sec and new_clips:
            new_clips[-1], remaining = _extend_item(new_clips[-1], take)
            if remaining <= 0.05:
                break
            if remaining < min_tail_sec:
                break
            take = min(_clip_duration(item.clip), remaining)
        if take >= min_tail_sec:
            new_clips.append(_trim_item(item, take))
            remaining -= take

    reason = f"{target_reason}; {decision}; duration={product_take:.2f}s"
    console.log(f"[green]✓[/green] product insert planned at {snapped_time:.2f}s via {reason}")
    return new_clips, reason


def _render_plan_from_clips(
    clips: list[_TimelineClip],
    *,
    strategy: BrollStrategy,
    target_duration: float,
    edit_profile: str,
    soft_mode: bool,
    warnings: list[str],
    product_insert_reason: str | None,
) -> RenderPlan:
    render_clips: list[RenderPlanClip] = []
    timeline = 0.0
    for index, item in enumerate(clips):
        duration = _clip_duration(item.clip)
        if duration <= 0.05:
            continue
        timeline_end = timeline + duration
        render_clips.append(
            RenderPlanClip(
                sequence_index=len(render_clips),
                asset_id=item.clip.asset_id,
                file=item.clip.file,
                source_start=round(item.clip.start, 3),
                source_end=round(item.clip.end, 3),
                timeline_start=round(timeline, 3),
                timeline_end=round(timeline_end, 3),
                duration_sec=round(duration, 3),
                rhythm_role=item.rhythm_role,
                placement_reason=item.placement_reason,
                block_kind=item.block_kind,
                block_reason=item.block_reason,
                is_product_insert=item.is_product_insert,
                asset_snapshot=_asset_snapshot(item.entry),
            )
        )
        timeline = timeline_end

    return RenderPlan(
        edit_profile=edit_profile,
        strategy=strategy,
        target_duration_sec=round(target_duration, 3),
        planned_duration_sec=round(timeline, 3),
        soft_rules=soft_mode,
        clips=render_clips,
        warnings=warnings,
        product_insert_reason=product_insert_reason,
    )


def render_plan_to_broll_clips(render_plan: RenderPlan) -> list[BRollClip]:
    return [
        BRollClip(
            asset_id=clip.asset_id,
            file=clip.file,
            start=clip.source_start,
            end=clip.source_end,
        )
        for clip in render_plan.clips
    ]


def _select_motion_preset(
    clip: RenderPlanClip,
    allowed_presets: list[MotionPreset],
) -> MotionPreset | None:
    snapshot = clip.asset_snapshot
    if snapshot is None:
        return None

    candidates = [preset for preset in allowed_presets if preset != "static"]
    # Subtitles always render at the top, so any motion that pushes the frame
    # upwards would collide with the subtitle band — drop push_up unconditionally.
    candidates = [preset for preset in candidates if preset != "push_up"]
    if not candidates:
        return None

    if clip.block_kind == "demo":
        preferred = [
            "zoom_in_soft",
            "zoom_out_soft",
            "drift_right",
            "drift_left",
            "push_up",
            "push_down",
        ]
    elif clip.block_kind == "transition":
        preferred = [
            "drift_right",
            "drift_left",
            "zoom_in_soft",
            "zoom_out_soft",
            "push_up",
            "push_down",
        ]
    elif clip.block_kind == "turn":
        preferred = [
            "zoom_in_soft",
            "drift_right",
            "drift_left",
            "zoom_out_soft",
            "push_up",
            "push_down",
        ]
    elif clip.rhythm_role == "anchor":
        preferred = [
            "zoom_in_soft",
            "zoom_out_soft",
            "drift_right",
            "drift_left",
            "push_up",
            "push_down",
        ]
    elif snapshot.shot_scale == "wide":
        preferred = [
            "drift_right",
            "drift_left",
            "zoom_in_soft",
            "zoom_out_soft",
            "push_up",
            "push_down",
        ]
    else:
        preferred = [
            "zoom_in_soft",
            "zoom_out_soft",
            "drift_right",
            "drift_left",
            "push_up",
            "push_down",
        ]

    if clip.sequence_index % 2:
        preferred = [
            "zoom_out_soft" if preset == "zoom_in_soft"
            else "zoom_in_soft" if preset == "zoom_out_soft"
            else "drift_left" if preset == "drift_right"
            else "drift_right" if preset == "drift_left"
            else "push_down" if preset == "push_up"
            else "push_up" if preset == "push_down"
            else preset
            for preset in preferred
        ]

    for preset in preferred:
        if preset in candidates:
            return preset
    return candidates[0]


def build_motion_plan(render_plan: RenderPlan, cfg: Config) -> RenderPlan:
    motion = cfg.motion
    if not motion.enabled:
        return render_plan

    default_profile_name = "balanced"
    default_profile = cfg.motion_profiles.get(default_profile_name, MotionProfileConfig())
    updated_clips: list[RenderPlanClip] = []
    eligible_seen = 0

    for clip in render_plan.clips:
        snapshot = clip.asset_snapshot
        motion_preset: MotionPreset | None = None
        motion_strength: float | None = None
        motion_reason: str | None = None
        profile_name = default_profile_name
        profile = default_profile

        if clip.is_product_insert:
            motion_reason = "motion skipped for product insert"
        elif snapshot is None:
            motion_reason = "motion skipped: asset snapshot missing"
        elif snapshot.motion_allowed is False:
            motion_reason = "motion skipped: asset metadata disables motion"
        elif clip.duration_sec < motion.min_clip_duration_sec:
            motion_reason = (
                f"motion skipped: clip shorter than {motion.min_clip_duration_sec:.1f}s"
            )
        else:
            if snapshot.effect_profile_override and snapshot.effect_profile_override in cfg.motion_profiles:
                profile_name = snapshot.effect_profile_override
                profile = cfg.motion_profiles[profile_name]

            allowed_presets = snapshot.allowed_motion_presets or ["static"]
            motion_preset = _select_motion_preset(clip, allowed_presets)
            if motion_preset is None:
                motion_reason = "motion skipped: no safe non-static preset"
            else:
                motion_strength = profile.intensity_multiplier
                if snapshot.motion_intensity_cap is not None:
                    motion_strength = min(motion_strength, snapshot.motion_intensity_cap)
                if motion_strength <= 0:
                    motion_reason = "motion skipped: intensity cap resolved to zero"
                    motion_preset = None
                    motion_strength = None
                    updated_clips.append(
                        clip.model_copy(
                            update={
                                "motion_preset": motion_preset,
                                "motion_strength": motion_strength,
                                "motion_reason": motion_reason,
                            }
                        )
                    )
                    continue

                should_apply = (
                    eligible_seen % profile.provocative_every_n == 0
                    and (not profile.anchor_only or clip.rhythm_role == "anchor")
                )
                cadence_reason = (
                    f"profile {profile_name}; cadence {profile.provocative_every_n}; "
                    f"anchor_only={profile.anchor_only}"
                )

                if should_apply:
                    motion_reason = (
                        f"{cadence_reason}; preset {motion_preset}; strength {motion_strength:.2f}"
                    )
                else:
                    motion_reason = f"motion skipped: cadence gate ({cadence_reason})"
                    motion_preset = None
                    motion_strength = None
                eligible_seen += 1

        updated_clips.append(
            clip.model_copy(
                update={
                    "motion_preset": motion_preset,
                    "motion_strength": motion_strength,
                    "motion_reason": motion_reason,
                }
            )
        )

    return render_plan.model_copy(update={"clips": updated_clips})


def build_transition_plan(render_plan: RenderPlan, cfg: Config) -> list[RenderPlanTransition]:
    transition = cfg.transition
    if not transition.enabled or transition.kind == "cut" or transition.duration_sec <= 0:
        return []

    transitions: list[RenderPlanTransition] = []
    for previous_clip, next_clip in zip(render_plan.clips, render_plan.clips[1:]):
        if transition.skip_product_insert and (
            previous_clip.is_product_insert or next_clip.is_product_insert
        ):
            continue
        if (
            previous_clip.duration_sec < transition.min_clip_duration_sec
            or next_clip.duration_sec < transition.min_clip_duration_sec
        ):
            continue
        if min(previous_clip.duration_sec, next_clip.duration_sec) < transition.duration_sec * 3:
            continue

        transitions.append(
            RenderPlanTransition(
                from_sequence_index=previous_clip.sequence_index,
                to_sequence_index=next_clip.sequence_index,
                timeline_start=round(next_clip.timeline_start, 3),
                duration_sec=round(transition.duration_sec, 3),
                kind=transition.kind,
                reason="eligible b-roll cut; product insert boundaries skipped",
            )
        )
    return transitions


def plan_broll(
    *,
    strategy: BrollStrategy,
    target_duration: float,
    cfg: Config,
    transcript: Transcript | None = None,
    product_insert: ProductInsert | None = None,
    product_break_after_token: int | None = None,
    tags: list[str] | None = None,
    ids_override: list[str] | None = None,
) -> RenderPlan:
    if target_duration <= 0:
        raise ValueError("target_duration must be positive")

    pool = resolve_broll_pool(strategy, tags=tags, ids_override=ids_override)
    soft_mode = not library_supports_full_rules(pool, cfg)
    rhythm = _active_rhythm(cfg)
    pattern = rhythm.pattern
    warnings: list[str] = []
    global_use_counts = load_usage_counts()
    recent_history = load_broll_recent_history()
    recent_start_assets = {
        asset_id
        for row in recent_history
        for asset_id in row[: cfg.broll_rotation.recent_start_window]
    }
    recent_anywhere_assets = {
        asset_id
        for row in recent_history
        for asset_id in row
    }

    if strategy == "manual":
        timeline_clips = _plan_manual_sequence(pool, target_duration, pattern)
    else:
        timeline_clips = _plan_role_sequence(
            pool,
            target_duration=target_duration,
            cfg=cfg,
            transcript=transcript,
            soft_mode=soft_mode,
            global_use_counts=global_use_counts,
            recent_start_assets=recent_start_assets,
            recent_anywhere_assets=recent_anywhere_assets,
            recent_start_penalty=cfg.broll_rotation.recent_start_penalty,
            recent_anywhere_penalty=cfg.broll_rotation.recent_anywhere_penalty,
        )

    timeline_clips, product_reason = _insert_product_clip(
        timeline_clips,
        transcript,
        product_insert,
        cfg.product_insert,
        product_break_after_token=product_break_after_token,
        warnings=warnings,
    )

    console.log(
        f"[green]✓[/green] Planned {len(timeline_clips)} b-roll timeline item(s), "
        f"target {target_duration:.2f}s "
        f"({'soft rules' if soft_mode else 'full rules'}, "
        f"rhythm={cfg.edit_profile.rhythm_profile})"
    )
    render_plan = _render_plan_from_clips(
        timeline_clips,
        strategy=strategy,
        target_duration=target_duration,
        edit_profile=cfg.edit_profile.rhythm_profile,
        soft_mode=soft_mode,
        warnings=warnings,
        product_insert_reason=product_reason,
    )

    updated_counts = dict(global_use_counts)
    for clip in render_plan.clips:
        if not clip.is_product_insert:
            updated_counts[clip.asset_id] = updated_counts.get(clip.asset_id, 0) + 1
    save_usage_counts(updated_counts)

    signature = [
        clip.asset_id
        for clip in render_plan.clips
        if not clip.is_product_insert
    ][: cfg.broll_rotation.recent_start_window]
    if cfg.broll_rotation.recent_history_size > 0 and signature:
        save_broll_recent_history(
            [signature, *recent_history][: cfg.broll_rotation.recent_history_size]
        )

    return render_plan
