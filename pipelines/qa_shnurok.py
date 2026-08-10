#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA gates for shnurok: word-sub overlap, format (incl. HDR), audio dedup.

Runs ALL gates against EVERY `final_*.mp4` in the render (classic AND bold,
not just whichever sorts first) — a style-specific regression (e.g. one
style's body subs overlapping) must not slip through because the other
style happened to pass.

Checks (per style):
  • WORD_SUBS   — word-by-word subtitle timing: end[i] <= start[i+1] (no overlap)
  • TITLE_BOUNDS— hook/CTA big-title boxes stay inside the safe frame (catches
                  titles clipped/running off the right edge — a visual defect the
                  timing/format gates can't see)
  • FORMAT      — resolution == 1080x1920, duration >= 1s, and color transfer is
                  NOT HDR (arib-std-b67/smpte2084) — an HDR-tagged final means
                  the source's HDR->SDR tonemap (src/shnurok/media.py) was
                  skipped, producing washed/flat output
  • DEDUP       — transcribed final audio has no back-to-back duplicate words

TITLE_BOUNDS is geometry-only: it verifies titles fit the frame, NOT that their
line breaks are semantically sensible (that stays a manual/visual review).

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

HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}


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


def check_title_bounds(ass_path: Path | str, safe_x: int = 16, safe_top: int = 20,
                       safe_bottom: int = 40, play_w: int = 1080, play_h: int = 1920) -> list[str]:
    """Flag big-title (Style==T) events whose estimated box leaves the safe frame.

    Parses each title Dialogue's alignment (\\anN), position (\\pos(x,y)) and font
    size (\\fsN override, else the Style's Fontsize), estimates the text box with
    the same width model the planner uses to clamp, and reports any box crossing
    the safe margins. Catches clipped / off-frame titles (a visual defect). Does
    NOT judge line-break quality. Empty list = pass.
    """
    ass_path = Path(ass_path)
    if not ass_path.exists():
        return [f"ASS file not found: {ass_path}"]
    from src.shnurok.titleplan import _est_width

    text = ass_path.read_text(encoding="utf-8", errors="replace")
    default_fs = 100
    for line in text.splitlines():
        if line.startswith("Style:"):
            f = [p.strip() for p in line[len("Style:"):].split(",")]
            if f and f[0] == "T":
                try:
                    default_fs = int(float(f[2]))
                except (IndexError, ValueError):
                    pass

    vio: list[str] = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10 or parts[3].strip() != "T":
            continue
        raw = parts[9]
        pos_m = re.search(r"\\pos\((\d+),(\d+)\)", raw)
        if not pos_m:
            continue
        an = int(m.group(1)) if (m := re.search(r"\\an(\d)", raw)) else 7
        x, y = int(pos_m.group(1)), int(pos_m.group(2))
        fs = int(m.group(1)) if (m := re.search(r"\\fs(\d+)", raw)) else default_fs
        disp = re.sub(r"\{[^}]*\}", "", raw).strip()
        if not disp:
            continue
        w = _est_width(disp, fs)
        h = fs * 1.2
        if an in (7, 4, 1):      # left-anchored
            left, right = x, x + w
        elif an in (9, 6, 3):    # right-anchored
            left, right = x - w, x
        else:                     # centered
            left, right = x - w / 2, x + w / 2
        if an in (7, 8, 9):      # top
            top, bottom = y, y + h
        elif an in (1, 2, 3):    # bottom
            top, bottom = y - h, y
        else:                     # middle
            top, bottom = y - h / 2, y + h / 2
        if left < safe_x or right > play_w - safe_x or top < safe_top or bottom > play_h - safe_bottom:
            vio.append(
                f"«{disp[:24]}» box [{int(left)},{int(top)}..{int(right)},{int(bottom)}] "
                f"outside safe frame ({safe_x}..{play_w - safe_x} x {safe_top}..{play_h - safe_bottom})"
            )
    return vio


def qa_video(path: Path | str) -> dict:
    """Check video format: resolution, duration, and HDR-not-tonemapped.

    Concretely checks: resolution == 1080x1920, duration >= 1s, and color
    transfer is not an HDR transfer (arib-std-b67/smpte2084) — a correctly
    built shnurok final is always tagged bt709 (see src/shnurok/media.py);
    an HDR-tagged final means the tonemap step was skipped somewhere.

    Returns dict with keys:
      - ok: bool (no violations)
      - width, height: int (pixels)
      - duration: float (seconds)
      - is_vertical: bool (height > width)
      - codec: str (e.g. "h264")
      - color_transfer: str (e.g. "bt709", "" if unknown)
      - violations: list[str] (empty = pass)
    """
    path = Path(path)
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,codec_name,color_transfer:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        return {"ok": False, "width": 0, "height": 0, "duration": 0.0,
                "is_vertical": False, "codec": "", "color_transfer": "",
                "violations": [f"ffprobe error: {r.stderr.strip()}"]}
    data = json.loads(r.stdout or "{}")
    st = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    w = int(st.get("width") or 0)
    h = int(st.get("height") or 0)
    dur = float(fmt.get("duration") or 0.0)
    trc = st.get("color_transfer") or ""
    vio = []
    if (w, h) != (1080, 1920):
        vio.append(f"resolution {w}x{h} != 1080x1920")
    if dur < 1.0:
        vio.append(f"duration {dur:.2f}s too short")
    if trc in HDR_TRANSFERS:
        vio.append(f"HDR transfer {trc} not tonemapped to bt709")
    return {"ok": not vio, "width": w, "height": h, "duration": dur,
            "is_vertical": h > w, "codec": st.get("codec_name"), "color_transfer": trc,
            "violations": vio}


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

    # Every final_*.mp4 (one per style, e.g. final_classic.mp4/final_bold.mp4)
    # gets ALL gates run against it — a regression in one style must not
    # slip through because a different style happened to be checked.
    finals = sorted(d.glob("final_*.mp4"))
    if not finals:
        print(f"FAIL: no final_*.mp4 found in {d}")
        return 1

    print(f"\nQA gates — {args.slug} ({len(finals)} style(s))")

    any_fail = False
    for final_video in finals:
        # Derive style from filename (final_classic.mp4 -> classic)
        style = final_video.stem.replace("final_", "")
        ass_file = d / f"body_subs_{style}.ass"

        print(f"\n[{style}] {final_video.name}")

        # Check word subs
        word_sub_vio = []
        if ass_file.exists():
            word_sub_vio = check_single_word_subs(ass_file)
            if word_sub_vio:
                print(f"  WORD_SUBS: FAIL")
                for v in word_sub_vio:
                    print(f"    - {v}")
            else:
                print(f"  WORD_SUBS: PASS ✓")
        else:
            print(f"  WORD_SUBS: skipped (no {ass_file.name})")

        # Check title bounds (hook + CTA sidecar ASS) — titles must fit the frame
        title_vio = []
        checked_any = False
        for tname in (f"hook_front_{style}.ass", f"cta_titles_{style}.ass"):
            tp = d / tname
            if tp.exists():
                checked_any = True
                title_vio += [f"{tname}: {v}" for v in check_title_bounds(tp)]
        if not checked_any:
            print(f"  TITLE_BOUNDS: skipped (no hook_front_{style}.ass / cta_titles_{style}.ass)")
        elif title_vio:
            print(f"  TITLE_BOUNDS: FAIL")
            for v in title_vio[:6]:
                print(f"    - {v}")
        else:
            print(f"  TITLE_BOUNDS: PASS ✓")

        # Check format (resolution, duration, HDR)
        fmt = qa_video(final_video)
        if fmt["violations"]:
            print(f"  FORMAT: FAIL")
            for v in fmt["violations"]:
                print(f"    - {v}")
        else:
            print(f"  FORMAT: PASS ✓ ({fmt['width']}×{fmt['height']} {fmt['duration']:.1f}s, "
                  f"transfer={fmt['color_transfer'] or 'untagged'})")

        # Check audio dedup
        dedup_vio = qa_audio_dedup(final_video)
        if dedup_vio:
            print(f"  DEDUP: FAIL")
            for v in dedup_vio[:3]:  # Show first 3
                print(f"    - {v}")
            if len(dedup_vio) > 3:
                print(f"    ... and {len(dedup_vio) - 3} more")
        else:
            print(f"  DEDUP: PASS ✓")

        total_vio = len(word_sub_vio) + len(title_vio) + len(fmt["violations"]) + len(dedup_vio)
        if total_vio:
            print(f"  [{style}] ИТОГ: FAIL ({total_vio} violations)")
            any_fail = True
        else:
            print(f"  [{style}] ИТОГ: PASS ✓")

    print("-" * 60)

    if any_fail:
        print(f"ИТОГ: FAIL (one or more styles failed)\n")
        return 1
    else:
        print(f"ИТОГ: PASS ✓ ({len(finals)} style(s))\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
