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

from src.output_paths import latest_version_dir  # noqa: E402


def parse_ts(ts: str) -> float:
    """Convert ASS timestamp H:MM:SS.cc to seconds."""
    m = re.match(r"(\d+):(\d+):(\d+)\.(\d+)", ts)
    if not m:
        return 0.0
    h, mm, ss, cc = map(int, m.groups())
    return h * 3600 + mm * 60 + ss + cc / 100.0


def slug_dir(slug: str, version: str | None = None) -> Path:
    """Resolve output/<slug> to a concrete version folder (read-only, no mutation).

    Exits with clear error if directory not found (QA tool must not mutate state).
    """
    base = ROOT / "output" / slug
    if version:
        d = base / version
        if not d.is_dir():
            print(f"FAIL: version {version} not found under {base}")
            raise SystemExit(1)
        return d
    d = latest_version_dir(base)
    if d is None:
        print(f"FAIL: no renders under {base}")
        raise SystemExit(1)
    return d


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
    items: list[tuple[float, float, str]] = []  # (start, end, text)
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
        text_raw = parts[9]

        # Strip ASS override tags {..} from text for display
        text_clean = re.sub(r"\{[^}]*\}", "", text_raw).strip()

        start = parse_ts(start_str)
        end = parse_ts(end_str)
        items.append((start, end, text_clean))

    # Sort by start time
    items.sort(key=lambda x: x[0])

    # Check for overlaps: end[i] > start[i+1] + epsilon
    epsilon = 1e-6
    for i in range(len(items) - 1):
        s1, e1, t1 = items[i]
        s2, e2, t2 = items[i + 1]
        if e1 > s2 + epsilon:
            violations.append(
                f"overlap: end {e1:.3f} > start {s2:.3f} "
                f"(«{t1[:20]}» vs «{t2[:20]}»)"
            )

    return violations


def qa_video(path: Path | str) -> dict:
    """Check video format: dimensions, duration, codec.

    Returns dict with keys:
      - ok: bool (no violations)
      - width, height: int (pixels)
      - duration: float (seconds)
      - is_vertical: bool (height > width)
      - codec: str (e.g. "h264")
      - violations: list[str] (empty = pass)
    """
    path = Path(path)
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,codec_name:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        return {"ok": False, "width": 0, "height": 0, "duration": 0.0,
                "is_vertical": False, "codec": "",
                "violations": [f"ffprobe error: {r.stderr.strip()}"]}
    data = json.loads(r.stdout or "{}")
    st = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    w = int(st.get("width") or 0)
    h = int(st.get("height") or 0)
    dur = float(fmt.get("duration") or 0.0)
    vio = []
    if (w, h) != (1080, 1920):
        vio.append(f"resolution {w}x{h} != 1080x1920")
    if dur < 1.0:
        vio.append(f"duration {dur:.2f}s too short")
    return {"ok": not vio, "width": w, "height": h, "duration": dur,
            "is_vertical": h > w, "codec": st.get("codec_name"), "violations": vio}


def qa_audio_dedup(path: Path | str) -> list[str]:
    """Transcribe final audio and flag back-to-back duplicate words.

    Extracts audio to WAV, transcribes via src.transcribe, flags doubled words.
    Returns list of violations (empty = pass).
    """
    import tempfile
    from src.transcribe import transcribe

    path = Path(path)
    if not path.exists():
        return [f"File not found: {path}"]

    violations: list[str] = []
    try:
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "a.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(path),
                 "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                check=True
            )
            t = transcribe(wav, Path(td) / "t.json", language="ru", model_size="small")
        words = [w.word.strip().lower().strip(",.!?…—") for w in t.words]
        for i in range(1, len(words)):
            a, b = words[i - 1], words[i]
            if a and a == b and len(a) >= 4:  # min len avoids false "да да"/"нет нет"
                violations.append(f"doubled word '{a}' at position {i}")
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

    # Find final videos
    finals = list(d.glob("final_*.mp4"))
    if not finals:
        print(f"FAIL: no final_*.mp4 found in {d}")
        return 1

    final_video = finals[0]  # Take the first (usually final_subtitled.mp4 or final_titled.mp4)
    # Derive style from filename (final_classic.mp4 -> classic)
    style = final_video.stem.replace("final_", "")
    ass_file = d / f"body_subs_{style}.ass"

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
        print(f"FORMAT: PASS ✓ ({fmt['width']}×{fmt['height']} {fmt['duration']:.1f}s)")

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
