from __future__ import annotations

from pathlib import Path

from .schemas import RenderPlan, Transcript


def _fmt_time(value: float) -> str:
    return f"{value:05.2f}s"


def _non_product_clips(render_plan: RenderPlan):
    return [clip for clip in render_plan.clips if not clip.is_product_insert]


def _average_non_product_clip_duration(render_plan: RenderPlan) -> float | None:
    clips = _non_product_clips(render_plan)
    if not clips:
        return None
    return sum(clip.duration_sec for clip in clips) / len(clips)


def _min_non_product_clip_duration(render_plan: RenderPlan) -> float | None:
    clips = _non_product_clips(render_plan)
    if not clips:
        return None
    return min(clip.duration_sec for clip in clips)


def _short_clip_streak_max(render_plan: RenderPlan, threshold_sec: float = 1.1) -> int:
    longest = 0
    current = 0
    for clip in _non_product_clips(render_plan):
        if clip.duration_sec < threshold_sec:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _rest_anchor_clips(render_plan: RenderPlan):
    return [
        clip
        for clip in render_plan.clips
        if clip.is_product_insert
        or (clip.rhythm_role == "anchor" and clip.duration_sec >= 2.4)
    ]


def _max_gap_between_rest_anchors(render_plan: RenderPlan) -> float | None:
    if not render_plan.clips:
        return None
    markers = [0.0]
    markers.extend(clip.timeline_start for clip in _rest_anchor_clips(render_plan))
    markers.append(render_plan.planned_duration_sec)
    markers = sorted(set(round(marker, 3) for marker in markers))
    if len(markers) < 2:
        return None
    return max(end - start for start, end in zip(markers, markers[1:]))


def _fmt_metric(value: float | None) -> str:
    if value is None:
        return "`n/a`"
    return f"`{round(value, 3)}`"


def _clip_line(render_plan: RenderPlan, index: int) -> str:
    clip = render_plan.clips[index]
    snapshot = clip.asset_snapshot
    tags: list[str] = []
    if clip.rhythm_role:
        tags.append(clip.rhythm_role)
    if clip.is_product_insert:
        tags.append("product")
    if snapshot is not None:
        if snapshot.shot_scale:
            tags.append(snapshot.shot_scale)
        if snapshot.scene_group:
            tags.append(snapshot.scene_group)
    if clip in _rest_anchor_clips(render_plan):
        tags.append("rest")
    if clip.block_kind:
        tags.append(f"block:{clip.block_kind}")

    return (
        f"{index:02d}. {_fmt_time(clip.timeline_start)}-{_fmt_time(clip.timeline_end)} "
        f"{clip.asset_id} [{', '.join(tags) or 'no-tags'}]\n"
        f"    placement: {clip.placement_reason}\n"
        f"    block: {clip.block_reason or 'none'}"
    )


def build_render_diagnostics(render_plan: RenderPlan, transcript: Transcript) -> str:
    lines = [
        "# Render Diagnostics",
        "",
        "## Summary",
        f"- edit_profile: `{render_plan.edit_profile}`",
        f"- strategy: `{render_plan.strategy}`",
        f"- look_profile: `{render_plan.look_profile or 'edit-profile'}`",
        f"- target_duration_sec: `{render_plan.target_duration_sec}`",
        f"- planned_duration_sec: `{render_plan.planned_duration_sec}`",
        f"- transcript_duration_sec: `{round(transcript.duration, 3)}`",
        f"- soft_rules: `{render_plan.soft_rules}`",
        f"- clips: `{len(render_plan.clips)}`",
        f"- blocks: `{len(render_plan.blocks)}`",
        f"- average_non_product_clip_duration: {_fmt_metric(_average_non_product_clip_duration(render_plan))}",
        f"- min_non_product_clip_duration: {_fmt_metric(_min_non_product_clip_duration(render_plan))}",
        f"- short_clip_streak_max: `{_short_clip_streak_max(render_plan)}`",
        f"- rest_anchor_count: `{len(_rest_anchor_clips(render_plan))}`",
        f"- max_gap_between_rest_anchors: {_fmt_metric(_max_gap_between_rest_anchors(render_plan))}",
        f"- tts_model_id: `{render_plan.tts_model_id or 'n/a'}`",
        f"- tts_preset: `{render_plan.tts_preset or 'n/a'}`",
        f"- tts_voice_id: `{render_plan.tts_voice_id or 'n/a'}`",
        f"- tts_generation_mode: `{render_plan.tts_generation_mode or 'n/a'}`",
        f"- tts_markup_dialect: `{render_plan.tts_markup_dialect or 'n/a'}`",
        f"- tts_output_format: `{render_plan.tts_output_format or 'n/a'}`",
        f"- tts_calls_count: `{render_plan.tts_calls_count if render_plan.tts_calls_count is not None else 'n/a'}`",
        f"- tts_used_voice_settings_override: `{render_plan.tts_used_voice_settings_override if render_plan.tts_used_voice_settings_override is not None else 'n/a'}`",
        f"- tts_inline_break_tags_count: `{render_plan.tts_inline_break_tags_count if render_plan.tts_inline_break_tags_count is not None else 'n/a'}`",
        f"- tts_dictionary_locators: `{', '.join(render_plan.tts_dictionary_locators) or 'none'}`",
        f"- tts_words_per_minute: `{render_plan.tts_words_per_minute if render_plan.tts_words_per_minute is not None else 'n/a'}`",
        f"- body_alignment_model: `{render_plan.body_alignment_model or 'n/a'}`",
        f"- hook_transcribe_model: `{render_plan.hook_transcribe_model or 'n/a'}`",
        f"- cta_transcribe_model: `{render_plan.cta_transcribe_model or 'n/a'}`",
        f"- music_start_sec: `{render_plan.music_start_sec if render_plan.music_start_sec is not None else 'n/a'}`",
        f"- music_fade_in_sec: `{render_plan.music_fade_in_sec if render_plan.music_fade_in_sec is not None else 'n/a'}`",
        f"- first_frame_black: `{render_plan.first_frame_black if render_plan.first_frame_black is not None else 'n/a'}`",
        f"- first_frame_mean_luma: `{render_plan.first_frame_mean_luma if render_plan.first_frame_mean_luma is not None else 'n/a'}`",
        f"- first_frame_black_pixel_ratio: `{render_plan.first_frame_black_pixel_ratio if render_plan.first_frame_black_pixel_ratio is not None else 'n/a'}`",
    ]
    if render_plan.product_insert_reason:
        lines.append(f"- product_insert_reason: {render_plan.product_insert_reason}")
    if render_plan.warnings:
        lines.append("")
        lines.append("## Warnings")
        lines.extend(f"- {warning}" for warning in render_plan.warnings)

    lines.extend(["", "## Subtitle Diagnostics"])
    if render_plan.subtitle_diagnostics:
        for item in render_plan.subtitle_diagnostics:
            lines.append(
                f"- {item.label}: transcript_tokens=`{item.transcript_tokens}`, "
                f"rendered_tokens=`{item.rendered_tokens}`, "
                f"missing=`{item.missing_tokens}`, "
                f"short_events=`{item.short_event_count}`, "
                f"double_content_word_chunks=`{item.double_content_word_chunks}`, "
                f"auto_fit_events=`{item.auto_fit_events}`"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Hook Montage"])
    if render_plan.hook_montage is not None:
        hook = render_plan.hook_montage
        lines.extend(
            [
                f"- enabled: `{hook.enabled}`",
                f"- source_duration_sec: `{hook.source_duration_sec}`",
                f"- final_duration_sec: `{hook.final_duration_sec}`",
                f"- effect_end_sec: `{hook.effect_end_sec}`",
                f"- kept_ranges: `{[(item.start, item.end) for item in hook.kept_ranges]}`",
                f"- removed_ranges: `{[(item.start, item.end) for item in hook.removed_ranges]}`",
            ]
        )
        if hook.overlays:
            for overlay in hook.overlays:
                lines.append(
                    f"- overlay: `{overlay.keyword}` word=`{overlay.word}` "
                    f"file=`{overlay.file}` placement=`{overlay.placement}` "
                    f"word_start=`{overlay.word_start_sec}` word_end=`{overlay.word_end_sec}` "
                    f"start=`{overlay.start_sec}` peak=`{overlay.peak_sec}` "
                    f"end=`{overlay.end_sec}` width_px=`{overlay.width_px}` "
                    f"opacity=`{overlay.opacity}`"
                )
        else:
            lines.append("- overlays: `none`")
        if hook.sfx_events:
            for event in hook.sfx_events:
                peak = event.peak_target_sec if event.peak_target_sec is not None else "n/a"
                lines.append(
                    f"- sfx: `{event.kind}` file=`{event.file}` "
                    f"start=`{event.timeline_start_sec}` trim_start=`{event.trim_start_sec}` "
                    f"duration=`{event.duration_sec}` gain_db=`{event.gain_db}` "
                    f"peak_target=`{peak}`"
                )
        else:
            lines.append("- sfx_events: `none`")
        if hook.motion_events:
            lines.append(f"- motion_events: `{', '.join(hook.motion_events)}`")
        else:
            lines.append("- motion_events: `none`")
        if hook.warnings:
            lines.extend(f"- warning: {warning}" for warning in hook.warnings)
    else:
        lines.append("- none")

    lines.extend(["", "## Clips"])
    lines.extend(_clip_line(render_plan, index) for index in range(len(render_plan.clips)))

    lines.extend(["", "## Blocks"])
    if render_plan.blocks:
        lines.extend(
            f"- {_fmt_time(block.timeline_start)}-{_fmt_time(block.timeline_end)} `{block.kind}` clips {block.clip_indices}: {block.reason}"
            for block in render_plan.blocks
        )
    else:
        lines.append("- none")

    lines.extend(["", "## Rest Anchors"])
    rest_anchors = _rest_anchor_clips(render_plan)
    if rest_anchors:
        lines.extend(
            f"- {_fmt_time(clip.timeline_start)}-{_fmt_time(clip.timeline_end)} "
            f"{clip.asset_id}: {clip.placement_reason}"
            for clip in rest_anchors
        )
    else:
        lines.append("- none")

    return "\n".join(lines) + "\n"


def write_render_diagnostics(
    render_plan: RenderPlan,
    transcript: Transcript,
    out_path: Path,
) -> Path:
    out_path.write_text(build_render_diagnostics(render_plan, transcript), encoding="utf-8")
    return out_path
