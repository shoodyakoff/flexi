from __future__ import annotations

import math
import subprocess
from pathlib import Path

from rich.console import Console

from .assets import get_asset_path

console = Console()


def _run(cmd: list[str], label: str) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed:\n{result.stderr}")


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


def preview_output_dir(root: Path, asset_id: str) -> Path:
    return root / "_annotation_previews" / asset_id


def render_broll_preview(
    asset_id: str,
    out_root: Path,
    *,
    frames: int = 4,
    preview_width: int = 360,
    preview_height: int = 640,
) -> tuple[Path, Path]:
    src = get_asset_path("broll", asset_id, prefer_ingest=False)
    out_dir = preview_output_dir(out_root, asset_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    duration = _ffprobe_duration(src)
    if duration <= 0:
        raise ValueError(f"Could not determine duration for {src}")

    cols = math.ceil(math.sqrt(frames))
    rows = math.ceil(frames / cols)
    fps = max(frames / duration, 0.1)

    contact_sheet_path = out_dir / f"{asset_id}_sheet.png"
    preview_mp4_path = out_dir / f"{asset_id}_preview.mp4"

    tile_filter = ",".join(
        [
            f"fps={fps:.6f}",
            f"scale={preview_width}:{preview_height}:force_original_aspect_ratio=decrease",
            f"pad={preview_width}:{preview_height}:(ow-iw)/2:(oh-ih)/2:black",
            f"tile={cols}x{rows}",
        ]
    )
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vf",
            tile_filter,
            "-frames:v",
            "1",
            str(contact_sheet_path),
        ],
        f"render contact sheet for {asset_id}",
    )

    preview_filter = ",".join(
        [
            f"scale={preview_width}:{preview_height}:force_original_aspect_ratio=decrease",
            f"pad={preview_width}:{preview_height}:(ow-iw)/2:(oh-ih)/2:black",
            "setsar=1",
        ]
    )
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vf",
            preview_filter,
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "24",
            "-pix_fmt",
            "yuv420p",
            str(preview_mp4_path),
        ],
        f"render preview mp4 for {asset_id}",
    )

    console.log(
        f"[green]✓[/green] Preview generated for {asset_id}: "
        f"{contact_sheet_path.name}, {preview_mp4_path.name}"
    )
    return contact_sheet_path, preview_mp4_path
