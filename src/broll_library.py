from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
import re

from rich.console import Console

from .assembly import normalize_clip
from .assets import ASSETS_DIR, PROJECT_ROOT, load_asset_meta, save_asset_meta
from .schemas import AssetEntry, Config

console = Console()

VIDEO_GLOBS = ("*.mp4", "*.MP4", "*.mov", "*.MOV", "*.m4v", "*.M4V")
_BROLL_ASSET_RE = re.compile(r"^broll(?:_\d+)?$", re.IGNORECASE)


@dataclass
class ScanSummary:
    scanned: int
    new_assets: int
    updated_assets: int
    normalized_assets: int
    needs_annotation: int
    meta_path: Path


def _ffprobe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def iter_broll_files() -> list[Path]:
    files: list[Path] = []
    broll_dir = ASSETS_DIR / "broll"
    for pattern in VIDEO_GLOBS:
        files.extend(path for path in broll_dir.glob(pattern) if _BROLL_ASSET_RE.fullmatch(path.stem))
    return sorted(files, key=lambda path: path.name.lower())


def _apply_broll_defaults(entry: AssetEntry, cfg: Config) -> AssetEntry:
    if entry.sequence_role is None and entry.shot_scale is not None:
        entry.sequence_role = {
            "wide": "establish",
            "medium": "action",
            "close": "detail",
            "detail": "detail",
        }[entry.shot_scale]

    # Phase 1 keeps motion as a derived field, not a manual annotation field.
    # Recompute it from the first-pass metadata on each scan so old placeholder
    # values do not survive after the clip has been annotated.
    motion_allowed = entry.shot_scale in {"wide", "medium"}
    if entry.subject_kind in {"product", "computer"}:
        motion_allowed = False
    entry.motion_allowed = motion_allowed

    presets = cfg.annotation.motion_presets_by_shot_scale.get(entry.shot_scale or "detail", ["static"])
    entry.allowed_motion_presets = presets if motion_allowed else ["static"]

    if entry.weight is None:
        entry.weight = cfg.annotation.default_weight

    entry.needs_annotation = bool(entry.missing_broll_annotation_fields())
    return entry


def scan_broll_library(cfg: Config, *, rebuild_ingest: bool = False, no_ingest: bool = False) -> ScanSummary:
    existing = load_asset_meta("broll") if (ASSETS_DIR / "broll" / "_meta.json").exists() else {}
    scanned_entries: dict[str, AssetEntry] = {}

    new_assets = 0
    updated_assets = 0
    normalized_assets = 0

    broll_files = iter_broll_files()
    if not broll_files:
        raise FileNotFoundError(f"No video files found in {ASSETS_DIR / 'broll'}")

    for src in broll_files:
        asset_id = src.stem
        previous = existing.get(asset_id)
        entry = previous.model_copy(deep=True) if previous is not None else AssetEntry(file=src.name, duration=0.0)
        entry.file = src.name

        duration_path = src
        if cfg.annotation.normalize_new_clips and not no_ingest:
            ingest_rel = Path(cfg.annotation.ingest_dir) / f"{asset_id}.mp4"
            ingest_abs = PROJECT_ROOT / ingest_rel
            needs_ingest = rebuild_ingest or previous is None or not ingest_abs.exists()
            if needs_ingest:
                ingest_abs.parent.mkdir(parents=True, exist_ok=True)
                normalize_clip(src, ingest_abs, cfg)
                normalized_assets += 1
            if ingest_abs.exists():
                entry.ingest_file = ingest_rel.as_posix()
                duration_path = ingest_abs

        entry.duration = round(_ffprobe_duration(duration_path), 2)
        _apply_broll_defaults(entry, cfg)

        scanned_entries[asset_id] = entry
        if previous is None:
            new_assets += 1
        elif entry.model_dump(mode="json", exclude_none=True) != previous.model_dump(mode="json", exclude_none=True):
            updated_assets += 1

    meta_path = save_asset_meta("broll", scanned_entries)
    needs_annotation = sum(1 for entry in scanned_entries.values() if entry.needs_annotation)
    return ScanSummary(
        scanned=len(scanned_entries),
        new_assets=new_assets,
        updated_assets=updated_assets,
        normalized_assets=normalized_assets,
        needs_annotation=needs_annotation,
        meta_path=meta_path,
    )
