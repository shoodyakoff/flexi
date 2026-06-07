from __future__ import annotations
import json
from pathlib import Path
from typing import Literal

from rich.console import Console

from .schemas import AssetEntry

console = Console()

AssetType = Literal["hooks", "broll", "ctas", "hook_overlays"]
PROJECT_ROOT = Path(__file__).parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
BROLL_META_DIRNAME = "meta"
_BROLL_USAGE_FILENAME = "_usage.json"
_BROLL_RECENT_HISTORY_FILENAME = "_recent_history.json"


def _broll_usage_path() -> Path:
    return ASSETS_DIR / "broll" / _BROLL_USAGE_FILENAME


def _broll_recent_history_path() -> Path:
    return ASSETS_DIR / "broll" / _BROLL_RECENT_HISTORY_FILENAME


def load_usage_counts() -> dict[str, int]:
    path = _broll_usage_path()
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {k: int(v) for k, v in raw.items()}


def save_usage_counts(counts: dict[str, int]) -> None:
    path = _broll_usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(counts, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def load_broll_recent_history() -> list[list[str]]:
    path = _broll_recent_history_path()
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    return [[str(asset_id) for asset_id in row] for row in raw if isinstance(row, list)]


def save_broll_recent_history(history: list[list[str]]) -> None:
    path = _broll_recent_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n")


def _meta_path(asset_type: AssetType) -> Path:
    return ASSETS_DIR / asset_type / "_meta.json"


def _broll_meta_dir() -> Path:
    return ASSETS_DIR / "broll" / BROLL_META_DIRNAME


def _broll_detail_path(asset_id: str) -> Path:
    return _broll_meta_dir() / f"{asset_id}.json"


def load_asset_meta(asset_type: AssetType) -> dict[str, AssetEntry]:
    path = _meta_path(asset_type)
    if not path.exists():
        raise FileNotFoundError(f"Meta file not found: {path}")
    raw = json.loads(path.read_text())
    if asset_type != "broll" or not _broll_meta_dir().exists():
        return {k: AssetEntry(**v) for k, v in raw.items()}

    merged_entries: dict[str, AssetEntry] = {}
    for asset_id, index_entry in raw.items():
        payload = dict(index_entry)
        detail_path = _broll_detail_path(asset_id)
        if detail_path.exists():
            payload.update(json.loads(detail_path.read_text()))
        merged_entries[asset_id] = AssetEntry(**payload)
    return merged_entries


def save_asset_meta(asset_type: AssetType, meta: dict[str, AssetEntry]) -> Path:
    path = _meta_path(asset_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    if asset_type != "broll":
        payload = {
            asset_id: entry.model_dump(mode="json", exclude_none=True)
            for asset_id, entry in meta.items()
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return path

    index_payload: dict[str, dict[str, object]] = {}
    detail_dir = _broll_meta_dir()
    detail_dir.mkdir(parents=True, exist_ok=True)

    for asset_id, entry in sorted(meta.items()):
        payload = entry.model_dump(mode="json", exclude_none=True)
        index_payload[asset_id] = {
            "file": payload.pop("file"),
            "duration": payload.pop("duration"),
        }
        detail_path = _broll_detail_path(asset_id)
        detail_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    for stale_path in detail_dir.glob("*.json"):
        if stale_path.stem not in meta:
            stale_path.unlink()

    path.write_text(json.dumps(index_payload, ensure_ascii=False, indent=2) + "\n")
    return path


def resolve_project_path(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else PROJECT_ROOT / path


def resolve_asset_entry_path(
    asset_type: AssetType,
    entry: AssetEntry,
    *,
    prefer_ingest: bool = True,
) -> Path:
    if asset_type == "broll" and prefer_ingest and entry.ingest_file:
        ingest_path = resolve_project_path(entry.ingest_file)
        if ingest_path.exists():
            return ingest_path

    direct_path = ASSETS_DIR / asset_type / entry.file
    if direct_path.exists():
        return direct_path

    if asset_type == "broll":
        fallback_path = resolve_project_path(entry.file)
        if fallback_path.exists():
            return fallback_path

    return direct_path


def get_asset_path(asset_type: AssetType, asset_id: str, *, prefer_ingest: bool = True) -> Path:
    meta = load_asset_meta(asset_type)
    if asset_id not in meta:
        raise ValueError(f"Asset '{asset_id}' not found in {asset_type}/_meta.json")
    path = resolve_asset_entry_path(asset_type, meta[asset_id], prefer_ingest=prefer_ingest)
    if not path.exists():
        raise FileNotFoundError(f"Asset file missing on disk: {path}")
    return path


def list_assets(asset_type: AssetType, filter_tags: list[str] | None = None) -> dict[str, AssetEntry]:
    meta = load_asset_meta(asset_type)
    if not filter_tags:
        return meta
    return {k: v for k, v in meta.items() if any(t in v.tags for t in filter_tags)}


def validate_assets(script_slug: str, hook_id: str, cta_id: str) -> None:
    get_asset_path("hooks", hook_id)
    get_asset_path("ctas", cta_id)
    console.log(f"[green]✓[/green] Assets validated: {hook_id}, {cta_id}")
