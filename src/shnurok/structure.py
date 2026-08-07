"""Structure inference for the shnurok route: classify dropped sources,
locate the hook/CTA windows in the talking-head audio, and propose a
b-roll order.

`split_hook_cta` is the pure, unit-tested core (see
tests/test_shnurok_structure.py). `inventory`, `locate_windows`, and
`order_broll` are integration glue around ffprobe / src.transcribe /
src.broll_library — they are exercised end-to-end by the Task 9
orchestrator, not unit-tested here.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

WordTiming = tuple[float, float, str]

_VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac"}
_MUSIC_HINTS = ("music", "bgm", "track")


def split_hook_cta(
    words: list[WordTiming],
    gap_thresh: float = 1.5,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Split word timings into a leading hook run and a trailing CTA span.

    Contract: `words` are word-level (start, end, text) timings of a
    talking-head clip that contains ONLY the hook followed by the CTA — the
    body is a separate voiceover file supplied by the client and is never
    part of this audio. So there is exactly one silence gap that matters:
    the one separating hook from CTA (plus possibly more gaps inside the
    CTA itself, e.g. a rhetorical pause before the final line).

    `words` is sorted by start. A run breaks wherever the silence between
    consecutive words exceeds `gap_thresh` seconds. Returns
    ((hook_start, hook_end), (cta_start, cta_end)):
    - hook = the span of the FIRST contiguous run (up to its trailing gap).
    - cta = ALL remaining runs merged into one span — from the start of the
      run right after the hook through the end of the last word. The CTA
      may legitimately contain internal pauses bigger than `gap_thresh`
      (e.g. "...доказать [pause] просто стиль"), and those must not slice
      the CTA into pieces or drop its tail.
    If there is only one run (no gap exceeds `gap_thresh`), hook and cta are
    the same span.

    Assumption: the hook itself is one contiguous run — its internal pauses
    (if any) stay under `gap_thresh`. If a clip has a genuinely long pause
    INSIDE the hook, this gap-based auto-split cannot tell that apart from
    the hook/CTA boundary; use the scripted `hook_text`/`cta_text` path in
    `locate_windows` instead of this auto-split for that clip.
    """
    if not words:
        raise ValueError("no words")
    runs, cur = [], [words[0]]
    for prev, w in zip(words, words[1:]):
        if w[0] - prev[1] > gap_thresh:
            runs.append(cur); cur = [w]
        else:
            cur.append(w)
    runs.append(cur)
    hook = (runs[0][0][0], runs[0][-1][1])
    # CTA is "everything after the hook's trailing gap", not strictly the
    # single last run: the CTA itself may contain an internal pause > thresh
    # (e.g. a rhetorical beat before the closing line), which would otherwise
    # split the CTA away from the rest of its tail. Merge all post-hook runs.
    tail = runs[1:] or runs
    cta = (tail[0][0][0], tail[-1][-1][1])
    return hook, cta


def _ffprobe_stream_kinds(path: Path) -> set[str]:
    """codec_type values present in `path` (e.g. {"video", "audio"})."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {result.stderr.strip()}")
    streams = json.loads(result.stdout or "{}").get("streams", [])
    return {s.get("codec_type") for s in streams}


def inventory(folder: Path) -> dict:
    """Classify every dropped file in `folder` by ffprobe.

    Images -> "graphic". Audio-only files -> "music" if the filename
    contains "music"/"bgm"/"track", else "voice". Video with an audio
    stream -> "talking_head". Video without an audio stream (silent
    screen/product footage) -> "broll". Orientation is NOT checked here —
    iPhone rotation tags lie, so the operating agent confirms it by eye
    (per CLAUDE.md), not this function. Dotfiles and files starting with
    "_" (e.g. "_meta.json") are skipped as config/hidden.
    """
    out: dict[str, list[Path]] = {
        "talking_head": [], "broll": [], "voice": [], "music": [], "graphic": [],
    }
    for path in sorted(Path(folder).iterdir()):
        if not path.is_file() or path.name.startswith((".", "_")):
            continue
        suffix = path.suffix.lower()
        if suffix in _IMAGE_EXTS:
            out["graphic"].append(path)
        elif suffix in _AUDIO_EXTS:
            bucket = "music" if any(h in path.stem.lower() for h in _MUSIC_HINTS) else "voice"
            out[bucket].append(path)
        elif suffix in _VIDEO_EXTS:
            kinds = _ffprobe_stream_kinds(path)
            out["talking_head" if "audio" in kinds else "broll"].append(path)
        # unrecognized extensions are left unclassified (not added anywhere)
    return {k: sorted(v) for k, v in out.items()}


def locate_windows(
    th_audio: Path,
    hook_text: str | None,
    cta_text: str | None,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Locate the hook and CTA windows in the talking-head audio.

    Scripted case (hook_text and cta_text both known): forced-align each
    text to `th_audio` via `src.transcribe.align_to_text` and take the span
    of its first/last aligned word as the window. Cache sidecars are
    written next to `th_audio` as "<stem>.hook_align.json" and
    "<stem>.cta_align.json" (align_to_text's own caching convention).
    Caveat: forced alignment assumes the given text accounts for the whole
    audio, so this is only accurate when `th_audio` is (or has been
    trimmed to) just that hook/CTA beat — the Task 9 orchestrator is
    responsible for passing an appropriately scoped audio file per call.

    Auto case (either text is None): free-transcribe the full audio via
    `src.transcribe.transcribe` and split the resulting word timings into
    the first/last contiguous run with `split_hook_cta`.
    """
    from src.transcribe import align_to_text, transcribe

    if hook_text is not None and cta_text is not None:
        hook_transcript = align_to_text(
            th_audio, hook_text, th_audio.with_suffix(".hook_align.json"),
        )
        cta_transcript = align_to_text(
            th_audio, cta_text, th_audio.with_suffix(".cta_align.json"),
        )
        hook = (hook_transcript.words[0].start, hook_transcript.words[-1].end)
        cta = (cta_transcript.words[0].start, cta_transcript.words[-1].end)
        return hook, cta

    transcript = transcribe(th_audio, th_audio.with_suffix(".transcript.json"))
    words = [(w.start, w.end, w.word) for w in transcript.words]
    return split_hook_cta(words)


def order_broll(
    clips: list[Path],
    transcript_words: list[WordTiming] | None = None,
) -> list[Path]:
    """Propose a b-roll clip order. Deterministic default: source/name order.

    `transcript_words` is accepted for interface symmetry with a future
    scoring pass (matching clip descriptions to nearby transcript content,
    e.g. via src.broll_library / rhythm_planner) but is intentionally
    unused here — keeping this simple and predictable. In practice the
    operating agent reviews and overrides the order by hand, so this is a
    starting point, not a final cut.
    """
    return sorted(clips)
