from __future__ import annotations

import random
import re
import subprocess
from collections import Counter
from pathlib import Path

from rich.console import Console

from .assets import get_asset_path, list_assets
from .schemas import (
    AssetEntry,
    BlockType,
    BRollClip,
    Config,
    ProductInsert,
    ProductInsertRules,
    Transcript,
)
from .text_matching import matches_anchor_root, normalize_word_token

console = Console()


def _ffprobe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def _strip_punct(word: str) -> str:
    """Стрипаем пунктуацию из слова перед матчингом."""
    return normalize_word_token(word)


def _library_supports_full_rules(
    pool: list[tuple[str, AssetEntry]],
    cfg: Config,
) -> bool:
    shot_scales = {entry.shot_scale for _, entry in pool if entry.shot_scale}
    scene_groups = {entry.scene_group for _, entry in pool if entry.scene_group}
    return (
        len(pool) >= cfg.annotation.pilot_target_count
        and len(shot_scales) >= 3
        and len(scene_groups) >= 4
    )


def _shot_scale_score(
    actual: str | None,
    desired_scales: tuple[str, ...],
    *,
    soft_mode: bool,
) -> float:
    if actual is None:
        return -1.0 if soft_mode else -3.0

    for rank, desired in enumerate(desired_scales):
        if actual == desired:
            return 6.0 - rank * 1.5

    # In the current pilot library there are no "close" clips yet.
    if actual == "detail" and "close" in desired_scales:
        return 4.75

    if actual == "medium" and "wide" in desired_scales:
        return 2.0

    return 0.5 if soft_mode else -2.5


def _role_score(actual: str | None, desired: str) -> float:
    if actual is None:
        return 0.0
    if actual == desired:
        return 2.5
    if desired == "detail" and actual == "action":
        return 0.5
    if desired == "action" and actual == "transition":
        return 0.5
    return -0.75


def _desired_energy() -> float:
    return 2.0


def _block_score(
    entry: AssetEntry,
    *,
    desired_block: BlockType | None,
    history: list[tuple[str, AssetEntry]],
) -> float:
    if desired_block is None:
        return 0.0

    score = 0.0
    previous_entry = history[-1][1] if history else None

    if desired_block == "explain":
        if entry.shot_scale in {"wide", "medium"}:
            score += 2.0
        if entry.sequence_role in {"establish", "action"}:
            score += 1.25
        if entry.energy == 1:
            score += 0.75
    elif desired_block == "demo":
        if entry.subject_kind in {"computer", "product", "hands"}:
            score += 2.5
        if entry.shot_scale in {"detail", "close", "medium"}:
            score += 1.75
        if entry.sequence_role in {"detail", "action"}:
            score += 1.0
    elif desired_block == "transition":
        if entry.sequence_role == "transition":
            score += 3.0
        if entry.shot_scale in {"wide", "medium"}:
            score += 0.75
        if entry.energy is not None and entry.energy >= 2:
            score += 0.5
        if (
            previous_entry is not None
            and entry.scene_group
            and previous_entry.scene_group
            and entry.scene_group != previous_entry.scene_group
        ):
            score += 1.75
    elif desired_block == "turn":
        if entry.shot_scale in {"medium", "wide"}:
            score += 1.75
        if entry.sequence_role in {"action", "establish"}:
            score += 1.25
        if entry.energy is not None and entry.energy >= 2:
            score += 0.5
        if (
            previous_entry is not None
            and entry.scene_group
            and previous_entry.scene_group
            and entry.scene_group != previous_entry.scene_group
        ):
            score += 1.5

    return score


def _candidate_score(
    asset_id: str,
    entry: AssetEntry,
    *,
    desired_block: BlockType | None,
    desired_scales: tuple[str, ...],
    desired_role: str,
    history: list[tuple[str, AssetEntry]],
    soft_mode: bool,
    global_use_counts: dict[str, int] | None = None,
    recent_start_assets: set[str] | None = None,
    recent_anywhere_assets: set[str] | None = None,
    recent_start_penalty: float = 0.0,
    recent_anywhere_penalty: float = 0.0,
) -> float:
    score = (entry.weight or 1.0) * 6.0
    score += _shot_scale_score(entry.shot_scale, desired_scales, soft_mode=soft_mode)
    score += _role_score(entry.sequence_role, desired_role)

    energy = entry.energy if entry.energy is not None else 2
    score += 2.0 - abs(energy - _desired_energy()) * 1.25
    score += _block_score(
        entry,
        desired_block=desired_block,
        history=history,
    )

    if history:
        last_asset_id, last_entry = history[-1]
        if asset_id == last_asset_id:
            score -= 8.0 if soft_mode else 18.0
        if entry.scene_group and last_entry.scene_group == entry.scene_group:
            score -= 4.0 if soft_mode else 8.0
        if entry.subject_kind and last_entry.subject_kind == entry.subject_kind:
            score -= 0.75 if soft_mode else 2.0
        if entry.shot_scale and last_entry.shot_scale == entry.shot_scale:
            score -= 1.0

    if len(history) >= 2:
        prev_entry = history[-1][1]
        prev_prev_entry = history[-2][1]
        if entry.scene_group and prev_entry.scene_group == entry.scene_group == prev_prev_entry.scene_group:
            score -= 9.0 if soft_mode else 24.0
        if entry.shot_scale and prev_entry.shot_scale == entry.shot_scale == prev_prev_entry.shot_scale:
            score -= 8.0 if soft_mode else 20.0
        if asset_id == history[-1][0] == history[-2][0]:
            score -= 12.0 if soft_mode else 30.0

    for lookback, (prev_asset_id, prev_entry) in enumerate(reversed(history[-4:]), start=1):
        if prev_asset_id == asset_id:
            score -= max(0.0, 8.0 - lookback * 1.5)
        if entry.scene_group and prev_entry.scene_group == entry.scene_group:
            score -= max(0.0, 5.5 - lookback)

    if global_use_counts:
        global_count = global_use_counts.get(asset_id, 0)
        score -= min(6.0, global_count * 0.8)

    if recent_start_assets and asset_id in recent_start_assets:
        score -= recent_start_penalty
    elif recent_anywhere_assets and asset_id in recent_anywhere_assets:
        score -= recent_anywhere_penalty

    score += random.gauss(0, 0.25)

    return score


def _clip_start_offset(entry: AssetEntry, take: float, use_index: int) -> float:
    available = max(0.0, entry.duration - take)
    if available <= 0.05:
        return 0.0

    slot_order = [0.0, 1.0, 0.5, 0.25, 0.75]
    ratio = slot_order[use_index % len(slot_order)]
    return round(available * ratio, 3)


def _build_clip(
    asset_id: str,
    entry: AssetEntry,
    take: float,
    *,
    use_index: int,
) -> BRollClip:
    start = _clip_start_offset(entry, take, use_index)
    end = min(entry.duration, start + take)
    if end - start < take and start > 0:
        start = max(0.0, end - take)
    return BRollClip(
        asset_id=asset_id,
        file=str(get_asset_path("broll", asset_id)),
        start=start,
        end=end,
    )


def resolve_broll_pool(
    strategy: str,
    *,
    tags: list[str] | None = None,
    ids_override: list[str] | None = None,
) -> list[tuple[str, AssetEntry]]:
    if strategy == "manual":
        if not ids_override:
            raise ValueError("broll_ids_override must be set when strategy is 'manual'")
        meta = {k: v for k, v in list_assets("broll").items() if k in ids_override}
        pool = [(asset_id, entry) for asset_id, entry in meta.items()]
        pool.sort(key=lambda item: ids_override.index(item[0]))
    elif strategy == "by_tags":
        if not tags:
            raise ValueError("broll_tags_preferred must be set when strategy is 'by_tags'")
        pool = list(list_assets("broll", filter_tags=tags).items())
    elif strategy == "auto":
        pool = list(list_assets("broll").items())
    else:
        raise ValueError(f"Unknown broll_strategy: '{strategy}'")

    if not pool:
        raise ValueError("No b-roll assets available for the given strategy/tags")
    return pool


def library_supports_full_rules(
    pool: list[tuple[str, AssetEntry]],
    cfg: Config,
) -> bool:
    return _library_supports_full_rules(pool, cfg)


def build_broll_clip(
    asset_id: str,
    entry: AssetEntry,
    take: float,
    *,
    use_index: int,
) -> BRollClip:
    return _build_clip(asset_id, entry, take, use_index=use_index)


def choose_broll_candidate(
    pool: list[tuple[str, AssetEntry]],
    *,
    desired_block: BlockType | None,
    desired_scales: tuple[str, ...],
    desired_role: str,
    history: list[tuple[str, AssetEntry]],
    soft_mode: bool,
    planned_duration_sec: float,
    remaining_duration_sec: float,
    use_counts: Counter[str],
    minimum_take_sec: float | None = None,
    global_use_counts: dict[str, int] | None = None,
    recent_start_assets: set[str] | None = None,
    recent_anywhere_assets: set[str] | None = None,
    recent_start_penalty: float = 0.0,
    recent_anywhere_penalty: float = 0.0,
) -> tuple[str, AssetEntry, float]:
    ranked_candidates: list[tuple[float, str, AssetEntry, float]] = []
    for asset_id, entry in pool:
        if entry.trim_policy == "keep_full":
            take = entry.duration
        else:
            take = min(entry.duration, planned_duration_sec)
        score = _candidate_score(
            asset_id,
            entry,
            desired_block=desired_block,
            desired_scales=desired_scales,
            desired_role=desired_role,
            history=history,
            soft_mode=soft_mode,
            global_use_counts=global_use_counts,
            recent_start_assets=recent_start_assets,
            recent_anywhere_assets=recent_anywhere_assets,
            recent_start_penalty=recent_start_penalty,
            recent_anywhere_penalty=recent_anywhere_penalty,
        )
        if entry.trim_policy == "keep_full":
            if entry.duration > remaining_duration_sec:
                score -= 50.0
            elif entry.duration > planned_duration_sec + 0.6:
                score -= 12.0
            else:
                score += 1.0
        if take < planned_duration_sec:
            score -= min(4.0, (planned_duration_sec - take) * 1.5)
        if minimum_take_sec is not None and take < minimum_take_sec:
            score -= 100.0
        if use_counts[asset_id] > 0:
            score -= min(4.0, use_counts[asset_id] * 0.75)
        ranked_candidates.append((score, asset_id, entry, take))

    ranked_candidates.sort(key=lambda item: (-item[0], item[1]))
    _, asset_id, entry, take = ranked_candidates[0]
    return asset_id, entry, max(0.25, take)


def _find_marker_break_time(
    transcript: Transcript,
    *,
    product_break_after_token: int | None,
) -> float | None:
    if product_break_after_token is None:
        return None
    if not transcript.words:
        console.log("[yellow]![/yellow] product_break marker ignored: transcript has no words")
        return None
    if product_break_after_token <= 0:
        console.log("[yellow]![/yellow] product_break marker ignored: no token before marker")
        return None
    if product_break_after_token >= len(transcript.words):
        console.log("[yellow]![/yellow] product_break marker ignored: marker is after transcript end")
        return None
    return transcript.words[product_break_after_token - 1].end


def _find_anchor_time(
    transcript: Transcript,
    overlay: ProductInsert,
) -> tuple[float | None, str | None]:
    if not overlay.anchor_root:
        return None, None

    for word in transcript.words:
        if matches_anchor_root(word.word, overlay.anchor_root):
            return max(0.0, word.start - overlay.lead_in_sec), f"anchor '{word.word}'"
    return None, None


def _find_pause_fallback_time(
    transcript: Transcript,
    rules: ProductInsertRules,
) -> float | None:
    if len(transcript.words) < 2:
        return None

    min_start = transcript.duration * rules.fallback_pause_after_ratio
    for previous_word, next_word in zip(transcript.words, transcript.words[1:]):
        gap = next_word.start - previous_word.end
        if previous_word.end >= min_start and gap >= rules.fallback_pause_min_gap_sec:
            return previous_word.end
    return None


def _resolve_insert_target_time(
    clips: list[BRollClip],
    transcript: Transcript,
    overlay: ProductInsert,
    rules: ProductInsertRules,
    *,
    product_break_after_token: int | None,
) -> tuple[float, str]:
    marker_time = _find_marker_break_time(
        transcript,
        product_break_after_token=product_break_after_token,
    )
    if marker_time is not None:
        return marker_time, "marker-driven break"

    anchor_time, anchor_reason = _find_anchor_time(transcript, overlay)
    if anchor_time is not None and anchor_reason is not None:
        return anchor_time, anchor_reason

    pause_time = _find_pause_fallback_time(transcript, rules)
    if pause_time is not None:
        return pause_time, "alignment pause fallback"

    timeline_total = sum(clip.end - clip.start for clip in clips)
    return timeline_total * rules.timeline_fallback_ratio, "timeline fallback"


def inject_highlight(
    clips: list[BRollClip],
    transcript: Transcript,
    overlay: ProductInsert,
    rules: ProductInsertRules,
    *,
    product_break_after_token: int | None = None,
) -> list[BRollClip]:
    """Врезает product insert в b-roll c marker-first и soft fallback логикой."""
    brand_path = Path(overlay.file)
    if not brand_path.is_absolute():
        brand_path = Path(__file__).parent.parent / overlay.file
    if not brand_path.exists():
        raise ValueError(f"highlight file not found: {brand_path}")
    brand_full_dur = _ffprobe_duration(brand_path)

    target_total = sum(c.end - c.start for c in clips)
    t_target, placement_reason = _resolve_insert_target_time(
        clips,
        transcript,
        overlay,
        rules,
        product_break_after_token=product_break_after_token,
    )

    timeline = 0.0
    target_idx: int | None = None
    target_clip_t_start = 0.0
    for i, clip in enumerate(clips):
        clip_dur = clip.end - clip.start
        if timeline + clip_dur > t_target:
            target_idx = i
            target_clip_t_start = timeline
            break
        timeline += clip_dur

    snap = overlay.snap_window_sec

    if target_idx is None:
        snapped_t = timeline
        before = clips
        after: list[BRollClip] = []
        decision = "append-at-end"
    else:
        spanning = clips[target_idx]
        offset_in_clip = t_target - target_clip_t_start
        clip_dur = spanning.end - spanning.start

        if offset_in_clip <= snap:
            snapped_t = target_clip_t_start
            before = clips[:target_idx]
            after = clips[target_idx + 1:]
            decision = f"snap-early (drop clip, shift {offset_in_clip:.2f}s earlier)"
        elif (clip_dur - offset_in_clip) <= snap:
            snapped_t = target_clip_t_start + clip_dur
            before = clips[:target_idx + 1]
            after = clips[target_idx + 1:]
            decision = f"snap-late (wait {clip_dur - offset_in_clip:.2f}s for clip end)"
        else:
            cut = BRollClip(
                asset_id=spanning.asset_id,
                file=spanning.file,
                start=spanning.start,
                end=spanning.start + offset_in_clip,
            )
            snapped_t = t_target
            before = clips[:target_idx] + [cut]
            after = clips[target_idx + 1:]
            decision = f"hard-cut at {offset_in_clip:.2f}s into clip"

    available_for_brand = target_total - snapped_t
    max_usable_brand = min(brand_full_dur, available_for_brand, rules.max_duration_sec)
    if max_usable_brand <= 0:
        console.log("[yellow]![/yellow] product insert has no room in b-roll timeline, skipping")
        return clips

    brand_end = min(max_usable_brand, rules.preferred_duration_max_sec)
    if brand_end < rules.min_duration_sec and max_usable_brand >= rules.min_duration_sec:
        brand_end = min(max_usable_brand, rules.min_duration_sec)

    brand_clip = BRollClip(
        asset_id="product_insert",
        file=str(brand_path),
        start=0.0,
        end=brand_end,
    )

    new_clips = before + [brand_clip]
    used = snapped_t + brand_end
    remaining = target_total - used
    for clip in after:
        if remaining <= 0:
            break
        clip_dur = clip.end - clip.start
        take = min(clip_dur, remaining)
        new_clips.append(
            BRollClip(
                asset_id=clip.asset_id,
                file=clip.file,
                start=clip.start,
                end=clip.start + take,
            )
        )
        remaining -= take

    console.log(
        f"[green]✓[/green] product insert placed at {snapped_t:.2f}s "
        f"via {placement_reason} ({decision}), insert_dur={brand_end:.2f}s"
    )
    return new_clips


def pick_broll(
    strategy: str,
    target_duration: float,
    cfg: Config,
    tags: list[str] | None = None,
    ids_override: list[str] | None = None,
) -> list[BRollClip]:
    from .rhythm_planner import plan_broll, render_plan_to_broll_clips

    render_plan = plan_broll(
        strategy=strategy,  # type: ignore[arg-type]
        target_duration=target_duration,
        cfg=cfg,
        tags=tags,
        ids_override=ids_override,
    )
    return render_plan_to_broll_clips(render_plan)
