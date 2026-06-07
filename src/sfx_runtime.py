from __future__ import annotations

from .schemas import Config, RenderPlan, RenderPlanSfxEvent


def _can_place_event(
    timeline_start: float,
    existing_events: list[RenderPlanSfxEvent],
    min_interval_sec: float,
) -> bool:
    return all(
        abs(timeline_start - event.timeline_start) >= min_interval_sec
        for event in existing_events
    )


def _cta_leadin_time(render_plan: RenderPlan, cfg: Config) -> float:
    latest_turn = next(
        (
            clip.timeline_start
            for clip in reversed(render_plan.clips)
            if not clip.is_product_insert and clip.block_kind == "turn"
        ),
        None,
    )
    fallback_time = max(
        0.0,
        render_plan.planned_duration_sec
        - cfg.sfx.cta_leadin_offset_from_end_sec
        - cfg.sfx.cta_duration_sec,
    )
    if latest_turn is None:
        return round(fallback_time, 3)
    return round(max(latest_turn, fallback_time), 3)


def build_sfx_plan(render_plan: RenderPlan, cfg: Config) -> RenderPlan:
    if not cfg.sfx.enabled:
        return render_plan

    min_interval_sec = cfg.sfx.min_interval_sec
    sfx_events: list[RenderPlanSfxEvent] = []
    key_punch_candidates = 0
    transition_candidates = 0

    # Transitions placed first so swoosh accents win over key_punch on the same boundary.
    for transition in render_plan.transitions:
        transition_candidates += 1
        if (transition_candidates - 1) % cfg.sfx.provocative_transition_every_n != 0:
            continue
        if not _can_place_event(
            transition.timeline_start,
            sfx_events,
            min_interval_sec,
        ):
            continue
        sfx_events.append(
            RenderPlanSfxEvent(
                sequence_index=len(sfx_events),
                kind="transition_soft",
                generator="transition_soft",
                timeline_start=round(transition.timeline_start, 3),
                duration_sec=0.55,
                gain_db=-10.0,
                reason="transition accent (swoosh sample)",
                sample_path="assets/sounds/swoosh.mp3",
            )
        )

    for clip in render_plan.clips:
        if clip.is_product_insert:
            if _can_place_event(
                clip.timeline_start,
                sfx_events,
                min_interval_sec,
            ):
                sfx_events.append(
                    RenderPlanSfxEvent(
                        sequence_index=len(sfx_events),
                        kind="product_insert",
                        generator="brand_ping",
                        timeline_start=round(clip.timeline_start, 3),
                        duration_sec=round(cfg.sfx.product_duration_sec, 3),
                        gain_db=cfg.sfx.product_gain_db,
                        reason="product insert accent",
                    )
                )
            continue

        if clip.block_kind in {"turn", "demo"}:
            key_punch_candidates += 1
            cadence = cfg.sfx.provocative_key_punch_every_n
            if (
                (key_punch_candidates - 1) % cadence == 0
                and _can_place_event(
                    clip.timeline_start,
                    sfx_events,
                    min_interval_sec,
                )
            ):
                sfx_events.append(
                    RenderPlanSfxEvent(
                        sequence_index=len(sfx_events),
                        kind="key_punch",
                        generator="punch_click",
                        timeline_start=round(clip.timeline_start, 3),
                        duration_sec=round(cfg.sfx.punch_duration_sec, 3),
                        gain_db=cfg.sfx.punch_gain_db,
                        reason=f"{clip.block_kind} key punch",
                    )
                )

        if clip.rhythm_role != "anchor":
            continue

        if (
            _can_place_event(
                clip.timeline_start,
                sfx_events,
                min_interval_sec,
            )
        ):
            block_label = clip.block_kind or "anchor"
            sfx_events.append(
                RenderPlanSfxEvent(
                    sequence_index=len(sfx_events),
                    kind="anchor_accent",
                    generator="accent_soft",
                    timeline_start=round(clip.timeline_start, 3),
                    duration_sec=round(cfg.sfx.anchor_duration_sec, 3),
                    gain_db=cfg.sfx.anchor_gain_db,
                    reason=f"{block_label} accent",
                )
            )

    cta_time = _cta_leadin_time(render_plan, cfg)
    if _can_place_event(
        cta_time,
        sfx_events,
        max(0.25, min_interval_sec * 0.5),
    ):
        sfx_events.append(
            RenderPlanSfxEvent(
                sequence_index=len(sfx_events),
                kind="cta_leadin",
                generator="cta_lift",
                timeline_start=cta_time,
                duration_sec=round(cfg.sfx.cta_duration_sec, 3),
                gain_db=cfg.sfx.cta_gain_db,
                reason="cta lead-in accent",
            )
        )

    sfx_events = sorted(sfx_events, key=lambda event: event.timeline_start)
    sfx_events = [
        event.model_copy(update={"sequence_index": index})
        for index, event in enumerate(sfx_events)
    ]
    return render_plan.model_copy(update={"sfx_events": sfx_events})
