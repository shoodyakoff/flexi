from __future__ import annotations

from .schemas import (
    BlockType,
    Config,
    RenderPlan,
    RenderPlanBeatMarker,
    RenderPlanBlock,
    RenderPlanClip,
    RenderPlanTransition,
)


def _infer_block_kind(
    clip: RenderPlanClip,
    previous_clip: RenderPlanClip | None,
) -> tuple[BlockType, str]:
    snapshot = clip.asset_snapshot
    previous_snapshot = previous_clip.asset_snapshot if previous_clip is not None else None

    if clip.is_product_insert:
        return "demo", "product insert is treated as a demo block"

    if previous_clip is not None and previous_clip.is_product_insert:
        return "turn", "first clip after product insert resets the sequence"

    if snapshot is None:
        return "explain", "default explain block"

    if snapshot.sequence_role == "transition":
        return "transition", "asset metadata marks the clip as a transition"

    if (
        snapshot.subject_kind in {"computer", "product"}
        and clip.rhythm_role in {"anchor", "detail"}
    ):
        return "demo", "computer/product-focused clip becomes a demo block"

    if clip.rhythm_role == "anchor":
        if previous_clip is not None and previous_clip.rhythm_role in {"support", "detail"}:
            return "turn", "support/detail cluster resolves into an anchor"
        if (
            snapshot.scene_group
            and previous_snapshot is not None
            and previous_snapshot.scene_group
            and snapshot.scene_group != previous_snapshot.scene_group
        ):
            return "turn", "scene-group change turns the anchor into a pivot"

    if (
        previous_snapshot is not None
        and snapshot.scene_group
        and previous_snapshot.scene_group
        and snapshot.scene_group != previous_snapshot.scene_group
        and clip.duration_sec <= 1.6
    ):
        return "transition", "short scene-group bridge clip"

    return "explain", "default explain block"


def _resolve_block_kind(
    clip: RenderPlanClip,
    previous_clip: RenderPlanClip | None,
) -> tuple[BlockType, str]:
    if clip.is_product_insert:
        return "demo", "product insert is treated as a demo block"

    if previous_clip is not None and previous_clip.is_product_insert:
        return "turn", "first clip after product insert resets the sequence"

    if clip.block_kind is not None:
        return clip.block_kind, clip.block_reason or "planner block template"

    return _infer_block_kind(clip, previous_clip)


def build_block_plan(render_plan: RenderPlan, cfg: Config) -> RenderPlan:
    del cfg

    updated_clips: list[RenderPlanClip] = []
    blocks: list[RenderPlanBlock] = []

    current_kind: BlockType | None = None
    current_reason = ""
    current_indices: list[int] = []
    current_start = 0.0
    current_end = 0.0

    def flush_block() -> None:
        nonlocal current_kind, current_reason, current_indices, current_start, current_end
        if current_kind is None or not current_indices:
            return
        blocks.append(
            RenderPlanBlock(
                sequence_index=len(blocks),
                kind=current_kind,
                timeline_start=round(current_start, 3),
                timeline_end=round(current_end, 3),
                clip_indices=current_indices.copy(),
                reason=current_reason,
            )
        )
        current_kind = None
        current_reason = ""
        current_indices = []
        current_start = 0.0
        current_end = 0.0

    previous_clip: RenderPlanClip | None = None
    for clip in render_plan.clips:
        kind, reason = _resolve_block_kind(clip, previous_clip)
        updated_clip = clip.model_copy(
            update={
                "block_kind": kind,
                "block_reason": reason,
            }
        )
        updated_clips.append(updated_clip)

        should_split = (
            current_kind is None
            or kind != current_kind
            or kind == "turn"
        )
        if should_split:
            flush_block()
            current_kind = kind
            current_reason = reason
            current_start = updated_clip.timeline_start
            current_end = updated_clip.timeline_end
            current_indices = [updated_clip.sequence_index]
        else:
            current_end = updated_clip.timeline_end
            current_indices.append(updated_clip.sequence_index)
            if current_reason != reason:
                current_reason = f"{current_reason}; {reason}"

        if kind == "turn":
            flush_block()

        previous_clip = updated_clip

    flush_block()
    return render_plan.model_copy(update={"clips": updated_clips, "blocks": blocks})


def _clip_lookup(clips: list[RenderPlanClip]) -> dict[int, int]:
    return {
        clip.sequence_index: index
        for index, clip in enumerate(clips)
    }


def _rebuild_block_timings(
    blocks: list[RenderPlanBlock],
    clips: list[RenderPlanClip],
) -> list[RenderPlanBlock]:
    if not blocks:
        return []

    rebuilt: list[RenderPlanBlock] = []
    for block in blocks:
        if not block.clip_indices:
            rebuilt.append(block)
            continue
        first_clip = clips[block.clip_indices[0]]
        last_clip = clips[block.clip_indices[-1]]
        rebuilt.append(
            block.model_copy(
                update={
                    "timeline_start": round(first_clip.timeline_start, 3),
                    "timeline_end": round(last_clip.timeline_end, 3),
                }
            )
        )
    return rebuilt


def _apply_cut_shift(
    previous_clip: RenderPlanClip,
    next_clip: RenderPlanClip,
    *,
    shift_sec: float,
    cfg: Config,
) -> tuple[RenderPlanClip, RenderPlanClip] | None:
    if abs(shift_sec) <= 0.001:
        return previous_clip, next_clip

    previous_snapshot = previous_clip.asset_snapshot
    next_snapshot = next_clip.asset_snapshot
    if previous_snapshot is None or next_snapshot is None:
        return None
    if previous_snapshot.trim_policy == "keep_full" or next_snapshot.trim_policy == "keep_full":
        return None

    prev_duration = previous_clip.duration_sec + shift_sec
    next_duration = next_clip.duration_sec - shift_sec
    if prev_duration < cfg.beat.min_clip_duration_sec or next_duration < cfg.beat.min_clip_duration_sec:
        return None

    if shift_sec > 0:
        extend_room = previous_snapshot.duration - previous_clip.source_end
        if extend_room + 0.001 < shift_sec:
            return None
        prev_update = {
            "source_end": round(previous_clip.source_end + shift_sec, 3),
            "timeline_end": round(previous_clip.timeline_end + shift_sec, 3),
            "duration_sec": round(prev_duration, 3),
        }
        next_update = {
            "source_start": round(next_clip.source_start + shift_sec, 3),
            "timeline_start": round(next_clip.timeline_start + shift_sec, 3),
            "duration_sec": round(next_duration, 3),
        }
    else:
        lead_in_room = next_clip.source_start
        if lead_in_room + 0.001 < abs(shift_sec):
            return None
        prev_update = {
            "source_end": round(previous_clip.source_end + shift_sec, 3),
            "timeline_end": round(previous_clip.timeline_end + shift_sec, 3),
            "duration_sec": round(prev_duration, 3),
        }
        next_update = {
            "source_start": round(next_clip.source_start + shift_sec, 3),
            "timeline_start": round(next_clip.timeline_start + shift_sec, 3),
            "duration_sec": round(next_duration, 3),
        }

    return (
        previous_clip.model_copy(update=prev_update),
        next_clip.model_copy(update=next_update),
    )


def _apply_cut_snaps(
    render_plan: RenderPlan,
    beat_markers: list[RenderPlanBeatMarker],
    cfg: Config,
) -> tuple[list[RenderPlanClip], list[RenderPlanTransition], list[RenderPlanBlock]]:
    clips = [clip.model_copy() for clip in render_plan.clips]
    clip_lookup = _clip_lookup(clips)
    updated_transitions: list[RenderPlanTransition] = []

    for transition in render_plan.transitions:
        previous_index = clip_lookup.get(transition.from_sequence_index)
        next_index = clip_lookup.get(transition.to_sequence_index)
        if previous_index is None or next_index is None:
            updated_transitions.append(transition)
            continue

        previous_clip = clips[previous_index]
        next_clip = clips[next_index]
        beat_info = _nearest_beat(next_clip.timeline_start, beat_markers)
        if beat_info is None:
            updated_transitions.append(transition)
            continue

        beat_time, delta = beat_info
        shift_sec = round(beat_time - next_clip.timeline_start, 3)
        snapped_to_beat = False
        reason = transition.reason

        if abs(shift_sec) <= cfg.beat.cut_snap_window_sec:
            shifted = _apply_cut_shift(
                previous_clip,
                next_clip,
                shift_sec=shift_sec,
                cfg=cfg,
            )
            if shifted is not None:
                previous_clip, next_clip = shifted
                clips[previous_index] = previous_clip
                clips[next_index] = next_clip
                snapped_to_beat = True
                reason = f"{reason}; cut beat-snapped"
            else:
                reason = f"{reason}; cut beat candidate rejected"
        else:
            reason = f"{reason}; nearest beat delta {delta:+.3f}s"

        final_beat_time, final_delta = _nearest_beat(next_clip.timeline_start, beat_markers) or (None, None)
        updated_transitions.append(
            transition.model_copy(
                update={
                    "timeline_start": round(next_clip.timeline_start, 3),
                    "beat_time": round(final_beat_time, 3) if final_beat_time is not None else None,
                    "beat_delta_sec": final_delta,
                    "snapped_to_beat": snapped_to_beat,
                    "reason": reason,
                }
            )
        )

    return clips, updated_transitions, _rebuild_block_timings(render_plan.blocks, clips)


def _beat_interval(cfg: Config) -> float:
    bpm = cfg.beat.provocative_bpm
    return 60.0 / bpm / cfg.beat.subdivision


def _nearest_beat(timestamp: float, beat_markers: list[RenderPlanBeatMarker]) -> tuple[float, float] | None:
    if not beat_markers:
        return None
    nearest = min(
        beat_markers,
        key=lambda marker: abs(marker.timeline_start - timestamp),
    )
    delta = round(timestamp - nearest.timeline_start, 3)
    return nearest.timeline_start, delta


def build_beat_plan(
    render_plan: RenderPlan,
    cfg: Config,
    *,
    music_enabled: bool,
) -> RenderPlan:
    if not cfg.beat.enabled or not music_enabled:
        return render_plan.model_copy(update={"beat_markers": []})

    interval = _beat_interval(cfg)
    beat_markers: list[RenderPlanBeatMarker] = []
    marker_time = 0.0
    beat_index = 0
    while marker_time <= render_plan.planned_duration_sec + 0.001:
        beat_markers.append(
            RenderPlanBeatMarker(
                sequence_index=len(beat_markers),
                beat_index=beat_index,
                timeline_start=round(marker_time, 3),
            )
        )
        beat_index += 1
        marker_time += interval
        if len(beat_markers) >= cfg.beat.max_markers:
            break

    updated_clips, updated_transitions, updated_blocks = _apply_cut_snaps(
        render_plan,
        beat_markers,
        cfg,
    )

    updated_sfx = []
    for event in render_plan.sfx_events:
        beat_info = _nearest_beat(event.timeline_start, beat_markers)
        if beat_info is None:
            updated_sfx.append(event)
            continue
        beat_time, delta = beat_info
        snapped = abs(delta) <= cfg.beat.snap_window_sec
        updated_sfx.append(
            event.model_copy(
                update={
                    "timeline_start": round(beat_time if snapped else event.timeline_start, 3),
                    "beat_time": round(beat_time, 3),
                    "beat_delta_sec": delta,
                    "snapped_to_beat": snapped,
                    "reason": (
                        f"{event.reason}; beat-snapped"
                        if snapped
                        else f"{event.reason}; nearest beat delta {delta:+.3f}s"
                    ),
                }
            )
        )

    updated_sfx = sorted(updated_sfx, key=lambda event: event.timeline_start)
    updated_sfx = [
        event.model_copy(update={"sequence_index": index})
        for index, event in enumerate(updated_sfx)
    ]

    return render_plan.model_copy(
        update={
            "clips": updated_clips,
            "blocks": updated_blocks,
            "beat_markers": beat_markers,
            "transitions": updated_transitions,
            "sfx_events": updated_sfx,
        }
    )
