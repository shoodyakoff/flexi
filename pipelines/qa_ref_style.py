#!/usr/bin/env python3
"""QA layer for ref-style / talking-head-dynamic renders.

Run this as a tester on every new video or version. It checks the finished mp4
plus its sidecar artifacts (``*.edit_plan.json``, ``*.render_diagnostics.json``)
against the failure modes this pipeline has actually hit:

  - wrong container/spec (not 1080x1920 / 30fps / no audio)            [FAIL]
  - dead air / silent head-turns left in (the buried-pause bug)        [FAIL/WARN]
  - HDR source squashed to SDR without tonemap (the "washed filter")   [WARN + manual frame]
  - black first/last frame                                            [FAIL]
  - edit-plan problems: unknown format, >2 same in a row, gaps         [FAIL/WARN]
  - subtitle text problems: latin-in-cyrillic ASR junk, no CTA         [WARN]
  - loudness too quiet/hot, true-peak clipping                         [WARN]

Usage:
    python pipelines/qa_ref_style.py --final output/<slug>/final_v5.mp4 [--source raw.MOV]

Exit code is the number of FAILs (0 = clean).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# ---- thresholds -------------------------------------------------------------
W, H, FPS = 1080, 1920, 30
SILENCE_NOISE_DB = "-30dB"
SILENCE_MIN = 0.40          # detector granularity
SILENCE_FAIL = 0.90         # any gap longer than this -> FAIL (dead air)
SILENCE_WARN = 0.45         # 0.45..0.90 -> WARN
TAIL_GRACE = 0.70           # ignore silence in the last N s (music fade tail)
HEAD_GRACE = 0.05
LUFS_TARGET = -16.0
LUFS_LO, LUFS_HI = -19.0, -12.0
TRUE_PEAK_MAX = -0.5
KNOWN_FORMATS = {
    "format_1_hook_metal", "format_2_framed_face", "format_3_turn_badge",
    "format_4_blue_demo", "format_5_lower_demo_cta",
}
HEAD_FORMATS = {"format_1_hook_metal", "format_2_framed_face", "format_3_turn_badge"}
CTA_HINTS = ("ссылк", "шапк", "профил", "переходи", "попробов")
HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
SYM = {PASS: "✓", WARN: "⚠", FAIL: "✗"}


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.rows.append((status, name, detail))

    def fails(self) -> int:
        return sum(1 for s, _, _ in self.rows if s == FAIL)

    def warns(self) -> int:
        return sum(1 for s, _, _ in self.rows if s == WARN)

    def render(self) -> str:
        lines = ["", "=" * 66, "  REF-STYLE RENDER QA", "=" * 66]
        for status, name, detail in self.rows:
            lines.append(f"  {SYM[status]} [{status}] {name}")
            if detail:
                for d in detail.splitlines():
                    lines.append(f"          {d}")
        lines.append("-" * 66)
        verdict = FAIL if self.fails() else (WARN if self.warns() else PASS)
        lines.append(f"  RESULT: {SYM[verdict]} {verdict}  "
                     f"({self.fails()} fail, {self.warns()} warn, {len(self.rows)} checks)")
        lines.append("=" * 66)
        return "\n".join(lines)


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True)


def ffprobe_json(path: Path) -> dict:
    res = _run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ])
    return json.loads(res.stdout or "{}")


def color_transfer(path: Path) -> str:
    res = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=color_transfer", "-of", "default=nw=1:nk=1", str(path),
    ])
    return (res.stdout or "").strip()


def detect_silences(path: Path) -> list[tuple[float, float]]:
    res = _run([
        "ffmpeg", "-hide_banner", "-i", str(path),
        "-af", f"silencedetect=noise={SILENCE_NOISE_DB}:d={SILENCE_MIN}", "-f", "null", "-",
    ])
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", res.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", res.stderr)]
    return list(zip(starts, ends))


def loudness(path: Path) -> tuple[float | None, float | None]:
    res = _run([
        "ffmpeg", "-hide_banner", "-i", str(path),
        "-af", "ebur128=peak=true", "-f", "null", "-",
    ])
    err = res.stderr
    i = re.findall(r"I:\s*(-?[0-9.]+)\s*LUFS", err)
    tp = re.findall(r"Peak:\s*(-?[0-9.]+)\s*dBFS", err)
    integ = float(i[-1]) if i else None
    peak = max(float(x) for x in tp) if tp else None
    return integ, peak


def black_intervals(path: Path) -> list[tuple[float, float]]:
    res = _run([
        "ffmpeg", "-hide_banner", "-i", str(path),
        "-vf", "blackdetect=d=0.05:pic_th=0.97", "-f", "null", "-",
    ])
    out = []
    for m in re.finditer(r"black_start:([0-9.]+) black_end:([0-9.]+)", res.stderr):
        out.append((float(m.group(1)), float(m.group(2))))
    return out


def max_consecutive(formats: list[str]) -> int:
    best = run = 0
    prev = None
    for f in formats:
        run = run + 1 if f == prev else 1
        prev = f
        best = max(best, run)
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", type=Path, required=True)
    ap.add_argument("--source", type=Path, default=None,
                    help="raw camera file (for HDR-tonemap check); else taken from diagnostics")
    args = ap.parse_args()
    final = args.final
    if not final.exists():
        print(f"final not found: {final}", file=sys.stderr)
        return 2

    plan_path = final.with_suffix(".edit_plan.json")
    diag_path = final.with_suffix(".render_diagnostics.json")
    plan = json.loads(plan_path.read_text()) if plan_path.exists() else None
    diag = json.loads(diag_path.read_text()) if diag_path.exists() else None

    r = Report()

    # ---- A. container / spec ----
    info = ffprobe_json(final)
    vstream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    astream = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    w, h = vstream.get("width"), vstream.get("height")
    fr = vstream.get("avg_frame_rate", "0/1")
    fps = (lambda n, d: float(n) / float(d) if float(d) else 0)(*fr.split("/")) if "/" in fr else 0
    dur = float(info.get("format", {}).get("duration", 0))
    r.add(PASS if (w, h) == (W, H) else FAIL, "resolution 1080x1920", f"{w}x{h}")
    r.add(PASS if abs(fps - FPS) < 0.2 else WARN, "fps 30", f"{fps:.2f}")
    r.add(PASS if astream else FAIL, "audio stream present",
          (astream or {}).get("codec_name", "none"))
    r.add(PASS if vstream.get("pix_fmt") == "yuv420p" else WARN,
          "pix_fmt yuv420p", vstream.get("pix_fmt", "?"))
    out_trc = color_transfer(final)
    r.add(PASS if out_trc in ("bt709", "") else WARN, "output transfer bt709 (SDR)", out_trc or "untagged")

    # ---- B. dead air ----
    sils = detect_silences(final)
    worst = 0.0
    flagged = []
    for s, e in sils:
        if s < HEAD_GRACE or e > dur - TAIL_GRACE:
            continue
        gap = e - s
        worst = max(worst, gap)
        if gap >= SILENCE_WARN:
            flagged.append(f"{s:.2f}-{e:.2f}s ({gap:.2f}s)")
    if worst >= SILENCE_FAIL:
        r.add(FAIL, "no dead air (>0.9s)", "DEAD AIR / head-turn left in:\n" + "\n".join(flagged))
    elif flagged:
        r.add(WARN, "no dead air (>0.45s)", "long pauses:\n" + "\n".join(flagged))
    else:
        r.add(PASS, "no dead air", f"longest mid-clip pause {worst:.2f}s")

    # ---- C. black frames ----
    blacks = black_intervals(final)
    edge_black = [b for b in blacks if b[0] < 0.15 or b[1] > dur - 0.15]
    if edge_black:
        r.add(FAIL, "no black first/last frame", str(edge_black))
    elif blacks:
        r.add(WARN, "black frames mid-clip", str(blacks))
    else:
        r.add(PASS, "no black frames")

    # ---- D. edit plan ----
    if plan:
        segs = plan.get("segments", [])
        formats = [s["format_id"] for s in segs]
        unknown = sorted(set(formats) - KNOWN_FORMATS)
        r.add(FAIL if unknown else PASS, "formats are known", str(unknown) if unknown else f"{len(set(formats))} distinct")
        mc = max_consecutive(formats)
        r.add(WARN if mc > 2 else PASS, "<=2 same format in a row", f"max run = {mc}")
        # coverage
        gaps = []
        ordered = sorted(segs, key=lambda s: s["start"])
        for a, b in zip(ordered, ordered[1:]):
            g = b["start"] - a["end"]
            if g > 1.0:
                gaps.append(f"{a['end']:.2f}->{b['start']:.2f} ({g:.2f}s)")
            if b["start"] < a["end"] - 0.05:
                gaps.append(f"overlap {b['start']:.2f}<{a['end']:.2f}")
        r.add(WARN if gaps else PASS, "plan coverage (no >1s gaps/overlap)",
              "\n".join(gaps) if gaps else f"{len(segs)} segments, {ordered[-1]['end']:.1f}s")
        # subtitle text from plan
        full = " ".join(s.get("text", "") for s in segs)
        latin = sorted(set(re.findall(r"\b[A-Za-z]{1,4}\b", full)) - {"АТС"})
        r.add(WARN if latin else PASS, "no latin-in-cyrillic ASR junk",
              f"suspicious tokens: {latin}" if latin else "clean")
        has_cta = any(any(h in s.get("text", "").lower() for h in CTA_HINTS)
                      for s in segs if s["format_id"] == "format_5_lower_demo_cta") \
            or any(h in full.lower() for h in CTA_HINTS)
        r.add(PASS if has_cta else WARN, "CTA present", "found" if has_cta else "no CTA keyword")
    else:
        r.add(WARN, "edit_plan.json found", f"missing: {plan_path.name}")

    # ---- E. products / b-roll ----
    if diag:
        products = diag.get("directed_render", {}).get("products", [])
        r.add(PASS if products else WARN, "product b-roll connected", f"{len(products)} clips")

    # ---- F. loudness ----
    integ, peak = loudness(final)
    if integ is not None:
        ok = LUFS_LO <= integ <= LUFS_HI
        r.add(PASS if ok else WARN, f"integrated loudness ({LUFS_LO}..{LUFS_HI} LUFS)",
              f"{integ:.1f} LUFS (target {LUFS_TARGET})")
    if peak is not None:
        r.add(PASS if peak <= TRUE_PEAK_MAX else WARN, "no true-peak clipping",
              f"peak {peak:.1f} dBFS")

    # ---- G. HDR / color (heuristic + manual frame) ----
    src = args.source or (Path(diag["source"]) if diag and diag.get("source") else None)
    src_trc = color_transfer(src) if src and src.exists() else ""
    color_frame = final.with_name(final.stem + ".qa_color.png")
    # extract a head-visible frame for manual color review
    t_head = None
    if plan:
        for s in plan.get("segments", []):
            if s["format_id"] in HEAD_FORMATS:
                t_head = (s["start"] + s["end"]) / 2
                break
    if t_head is None:
        t_head = min(1.2, dur / 2)
    _run(["ffmpeg", "-y", "-ss", f"{t_head:.2f}", "-i", str(final),
          "-frames:v", "1", "-q:v", "2", str(color_frame)])
    if src_trc in HDR_TRANSFERS:
        r.add(WARN, "HDR source -> tonemap (manual color check)",
              f"source is {src_trc} (HDR). Output must be properly tonemapped, NOT a naive\n"
              f"squash (washed/flat). Eyeball head frame: {color_frame.name}")
    else:
        r.add(PASS, "source color", f"transfer={src_trc or 'sdr/unknown'}; head frame: {color_frame.name}")

    print(r.render())
    return r.fails()


if __name__ == "__main__":
    raise SystemExit(main())
