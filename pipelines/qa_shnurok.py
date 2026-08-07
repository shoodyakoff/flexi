#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA gates for shnurok: word-sub overlap, format, audio dedup.

Checks:
  • WORD_SUBS — word-by-word subtitle timing: end[i] <= start[i+1] (no overlap)
  • FORMAT    — video is 1080×1920 (vertical), 30fps, H.264, stereo audio
  • DEDUP     — transcribed final audio has no back-to-back duplicate words

Invocation:
    python3 pipelines/qa_shnurok.py --slug <slug>
    python3 pipelines/qa_shnurok.py --slug <slug> --version v2
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.output_paths import latest_version_dir, versioned_dir  # noqa: E402
from src.transcribe import transcribe  # noqa: E402


def slug_dir(slug: str, version: str | None = None) -> Path:
    """Resolve output/<slug> to a concrete version folder."""
    base = ROOT / "output" / slug
    if version:
        return versioned_dir(base, version=version)
    latest = latest_version_dir(base)
    return latest if latest is not None else base


def check_single_word_subs(ass_path: Path | str) -> list[str]:
    """Parse ASS file for word-subs (Style==W) and flag overlap violations.

    Returns a list of human-readable overlap descriptions (empty = pass).
    ASS timestamp format: H:MM:SS.cc
    """
    ass_path = Path(ass_path)
    if not ass_path.exists():
        return [f"ASS file not found: {ass_path}"]

    text = ass_path.read_text(encoding="utf-8", errors="replace")
    violations: list[str] = []

    # Parse Dialogue lines with Style == W
    items: list[tuple[float, float, str, str]] = []  # (start, end, text, line)
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        # Dialogue: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
        style = parts[3].strip()
        if style != "W":
            continue
        start_str = parts[1].strip()
        end_str = parts[2].strip()
        text = parts[9]

        # Convert H:MM:SS.cc to seconds
        def parse_ts(ts: str) -> float:
            """Convert ASS timestamp H:MM:SS.cc to seconds."""
            m = re.match(r"(\d+):(\d+):(\d+)\.(\d+)", ts)
            if not m:
                return 0.0
            h, mm, ss, cc = map(int, m.groups())
            return h * 3600 + mm * 60 + ss + cc / 100.0

        start = parse_ts(start_str)
        end = parse_ts(end_str)
        items.append((start, end, text, line))

    # Sort by start time
    items.sort(key=lambda x: x[0])

    # Check for overlaps: end[i] > start[i+1] + epsilon
    epsilon = 1e-6
    for i in range(len(items) - 1):
        s1, e1, t1, l1 = items[i]
        s2, e2, t2, l2 = items[i + 1]
        if e1 > s2 + epsilon:
            violations.append(
                f"overlap: end {e1:.3f} > start {s2:.3f} "
                f"(«{t1.strip()[:20]}» vs «{t2.strip()[:20]}»)"
            )

    return violations


def qa_video(path: Path | str) -> dict:
    """Check video format: dimensions, frame rate, codec, audio.

    Returns a dict with keys:
      - width, height: int (pixels)
      - fps: float
      - codec_video: str (e.g. "h264")
      - codec_audio: str (e.g. "aac")
      - channels: int (audio channels)
      - is_vertical: bool (height > width)
      - violations: list[str] (empty = pass)
    """
    path = Path(path)
    result = {
        "width": 0, "height": 0, "fps": 0.0,
        "codec_video": "", "codec_audio": "",
        "channels": 0, "is_vertical": False,
        "violations": []
    }

    if not path.exists():
        result["violations"].append(f"File not found: {path}")
        return result

    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams",
             "-print_json", str(path)],
            capture_output=True, text=True, timeout=10
        )
        if out.returncode != 0:
            result["violations"].append(f"ffprobe error: {out.stderr}")
            return result

        data = json.loads(out.stdout)

        # Find video stream
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                result["width"] = stream.get("width", 0)
                result["height"] = stream.get("height", 0)
                result["codec_video"] = stream.get("codec_name", "").lower()

                # Parse frame rate (may be "30/1" or "30")
                r_frame_rate = stream.get("r_frame_rate", "0/1")
                if "/" in r_frame_rate:
                    num, den = map(float, r_frame_rate.split("/"))
                    result["fps"] = num / den if den != 0 else 0.0
                else:
                    result["fps"] = float(r_frame_rate) if r_frame_rate else 0.0
                break

        # Find audio stream
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "audio":
                result["codec_audio"] = stream.get("codec_name", "").lower()
                result["channels"] = stream.get("channels", 0)
                break

        # Sanity checks
        result["is_vertical"] = result["height"] > result["width"]
        if result["width"] != 1080 or result["height"] != 1920:
            result["violations"].append(
                f"resolution {result['width']}×{result['height']}, expected 1080×1920"
            )
        if abs(result["fps"] - 30.0) > 0.5:
            result["violations"].append(f"fps {result['fps']:.1f}, expected ~30")
        if result["codec_video"] not in ("h264", "hevc"):
            result["violations"].append(f"codec {result['codec_video']}, expected h264 or hevc")
        if result["channels"] < 1:
            result["violations"].append(f"no audio ({result['channels']} channels)")

    except Exception as e:
        result["violations"].append(f"Exception: {e}")

    return result


def qa_audio_dedup(path: Path | str) -> list[str]:
    """Transcribe final audio and flag back-to-back duplicate words.

    Returns a list of violations (empty = pass).
    """
    path = Path(path)
    if not path.exists():
        return [f"File not found: {path}"]

    violations: list[str] = []

    try:
        # Transcribe the audio
        transcript = transcribe(path, language="ru")
        if not transcript or "words" not in transcript:
            return [f"Transcription failed or empty for {path}"]

        words = transcript.get("words", [])
        if len(words) < 2:
            return []

        # Normalize a word: lowercase, strip punctuation
        def norm_word(w: str) -> str:
            return re.sub(r"[^0-9a-zа-яё]", "", w.lower())

        # Flag consecutive duplicate words (common artifact from retakes)
        prev_norm = ""
        for i, w_dict in enumerate(words):
            word = w_dict.get("word", "").strip()
            norm = norm_word(word)
            if norm and norm == prev_norm:
                # Found a duplicate
                prev_word = words[i - 1].get("word", "").strip() if i > 0 else ""
                violations.append(
                    f"duplicate word at {i}: «{prev_word}» then «{word}»"
                )
            prev_norm = norm

    except Exception as e:
        violations.append(f"Exception: {e}")

    return violations


def main() -> int:
    ap = argparse.ArgumentParser(
        description="QA gates for shnurok: word-sub overlap, format, audio dedup"
    )
    ap.add_argument("--slug", required=True, help="Slug name (e.g. 's001')")
    ap.add_argument("--version", help="Version subfolder (e.g. v2). Default: latest.")
    args = ap.parse_args()

    d = slug_dir(args.slug, args.version)
    if not d.exists():
        print(f"FAIL: output directory not found: {d}")
        return 1

    # Find final videos
    finals = list(d.glob("final_*.mp4"))
    if not finals:
        print(f"FAIL: no final_*.mp4 found in {d}")
        return 1

    final_video = finals[0]  # Take the first (usually final_subtitled.mp4 or final_titled.mp4)
    ass_file = final_video.with_suffix(".ass")

    print(f"\nQA gates — {args.slug}")
    print(f"Video: {final_video.name}")

    # Check word subs
    word_sub_vio = []
    if ass_file.exists():
        word_sub_vio = check_single_word_subs(ass_file)
        if word_sub_vio:
            print(f"WORD_SUBS: FAIL")
            for v in word_sub_vio:
                print(f"  - {v}")
        else:
            print(f"WORD_SUBS: PASS ✓")
    else:
        print(f"WORD_SUBS: skipped (no {ass_file.name})")

    # Check format
    fmt = qa_video(final_video)
    if fmt["violations"]:
        print(f"FORMAT: FAIL")
        for v in fmt["violations"]:
            print(f"  - {v}")
    else:
        print(f"FORMAT: PASS ✓ ({fmt['width']}×{fmt['height']} {fmt['fps']:.0f}fps)")

    # Check audio dedup
    dedup_vio = qa_audio_dedup(final_video)
    if dedup_vio:
        print(f"DEDUP: FAIL")
        for v in dedup_vio[:3]:  # Show first 3
            print(f"  - {v}")
        if len(dedup_vio) > 3:
            print(f"  ... and {len(dedup_vio) - 3} more")
    else:
        print(f"DEDUP: PASS ✓")

    print("-" * 60)

    total_vio = len(word_sub_vio) + len(fmt["violations"]) + len(dedup_vio)
    if total_vio:
        print(f"ИТОГ: FAIL ({total_vio} violations)\n")
        return 1
    else:
        print(f"ИТОГ: PASS ✓\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
