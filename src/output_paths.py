"""Versioned output layout shared by every render route.

One folder per video (the *slug*); each render is a numbered version
subfolder inside it (``v1``, ``v2``, ...). A ``latest`` symlink in the slug
folder always points at the newest version, so "where is my final" is a
stable path regardless of how many times the video was re-rendered.

    output/
      talking_head_002/
        v1/   final_subtitled.mp4 ...
        v2/   final_subtitled.mp4 ...
        latest -> v2

Every route computes ``output_root / slug`` as its base, then calls
:func:`versioned_dir` to get the concrete directory to render into. This keeps
versions of the same video together instead of spawning sibling folders
(``video1``, ``video1_v2``, ``video1_auto`` ...).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

VERSION_RE = re.compile(r"^v(\d+)$")
LATEST_LINK = "latest"


def existing_version_numbers(base_dir: Path) -> list[int]:
    """Sorted version numbers already present under ``base_dir`` (``v1`` -> 1)."""
    if not base_dir.is_dir():
        return []
    numbers: list[int] = []
    for child in base_dir.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        match = VERSION_RE.match(child.name)
        if match:
            numbers.append(int(match.group(1)))
    return sorted(numbers)


def next_version_name(base_dir: Path) -> str:
    """The next free ``vN`` name for ``base_dir`` (``v1`` on an empty base)."""
    numbers = existing_version_numbers(base_dir)
    return f"v{(numbers[-1] + 1) if numbers else 1}"


def update_latest(base_dir: Path, version_name: str) -> None:
    """Point ``base_dir/latest`` at ``version_name`` (relative symlink).

    Best-effort: if the filesystem refuses symlinks, the version folder is
    still usable, we just skip the convenience pointer.
    """
    link = base_dir / LATEST_LINK
    try:
        if link.is_symlink() or link.exists():
            if link.is_symlink() or link.is_file():
                link.unlink()
            else:
                # A real directory named "latest" is not ours to clobber.
                return
        link.symlink_to(version_name, target_is_directory=True)
    except OSError:
        pass


def versioned_dir(base_dir: Path, version: str | None = None) -> Path:
    """Return the directory to render into under ``base_dir`` and make it.

    ``base_dir`` is ``output/<slug>``. With ``version=None`` (the default) a new
    ``vN`` folder is created so the previous renders are preserved. Pass an
    explicit ``version`` (e.g. ``"v2"``) to render into / overwrite that exact
    version instead. The ``latest`` pointer is refreshed to the chosen version.
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    name = _normalize_version(version) if version else next_version_name(base_dir)
    target = base_dir / name
    target.mkdir(parents=True, exist_ok=True)
    update_latest(base_dir, name)
    return target


def latest_version_dir(base_dir: Path) -> Path | None:
    """Resolve ``base_dir/latest`` to its target version folder, if one exists.

    Call this *before* :func:`versioned_dir` if you want the previous render —
    ``versioned_dir`` repoints ``latest`` at the new version.
    """
    link = base_dir / LATEST_LINK
    if link.is_symlink() or link.exists():
        resolved = link.resolve()
        if resolved.is_dir():
            return resolved
    return None


def seed_reusable(previous_dir: Path | None, out_dir: Path, patterns: list[str]) -> list[Path]:
    """Copy cached artifacts (matching ``patterns``) from a previous version into
    a fresh one, so re-renders reuse them instead of recomputing.

    Used for expensive, input-determined outputs — Whisper transcripts, TTS
    audio — whose on-disk caches key off file existence in the render folder.
    No-op when there is no previous version. Returns the files copied.
    """
    if previous_dir is None or not previous_dir.is_dir() or previous_dir.resolve() == out_dir.resolve():
        return []
    copied: list[Path] = []
    for pattern in patterns:
        for src_file in sorted(previous_dir.glob(pattern)):
            if not src_file.is_file():
                continue
            target = out_dir / src_file.name
            if not target.exists():
                shutil.copy2(src_file, target)
                copied.append(target)
    return copied


def _normalize_version(version: str) -> str:
    """Accept ``2``, ``v2`` or ``V2`` and return the canonical ``v2``."""
    text = version.strip().lower()
    if text.startswith("v") and text[1:].isdigit():
        return f"v{int(text[1:])}"
    if text.isdigit():
        return f"v{int(text)}"
    # Allow arbitrary labels (e.g. "final") but keep them filesystem-safe.
    return re.sub(r"[^a-z0-9._-]+", "-", text) or "v1"
