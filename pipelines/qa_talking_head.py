#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA для нарезки talking-head: проверяет ТАЙМИНГ резов по звуку, а не на глаз.

Встроенный quality_report пайплайна смотрит только число слов в куске. Этот QA
ловит то, что реально портит ощущение от монтажа:

  • ХВОСТ   — тишина в конце фразы после того, как звук смолк (там видно, как
              человек отворачивает голову к тексту). Меряется по silencedetect.
  • ПОДРЕЗ  — рез попал на начало слова → слышно «обрубок» (онсет не успел).
  • ОБРЫВ   — рез прошёл ВНУТРИ слова, которое ещё звучало → «не договорил».
  • ЛИД     — сколько воздуха перед первым словом (для хука — намеренный «бит»).

Запуск:
    python3 pipelines/qa_talking_head.py --slug video1_v3
    python3 pipelines/qa_talking_head.py --source S.mov --plan plan.json --transcript words.json

Коды выхода: 0 — без FAIL; 1 — есть FAIL (подрез/обрыв).
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.output_paths import latest_version_dir, versioned_dir  # noqa: E402

TAIL_WARN = 0.40      # хвост тишины больше этого — предупреждение
CLIP_FAIL = 0.03      # подрез/обрыв больше этого — ошибка


def slug_dir(slug: str, version: str | None = None) -> Path:
    """Resolve output/<slug> to a concrete version folder.

    Versions of one video live in vN subfolders; --version picks one, otherwise
    we use the `latest` pointer. Falls back to the base folder for legacy flat
    layouts that predate versioning.
    """
    base = ROOT / "output" / slug
    if version:
        return versioned_dir(base, version=version)
    latest = latest_version_dir(base)
    return latest if latest is not None else base


def silences(source: Path, noise: float, min_sil: float) -> list[tuple[float, float]]:
    err = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(source),
         "-af", f"silencedetect=noise={noise}dB:d={min_sil}", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    out, cs = [], None
    for ln in err.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", ln)
        if m:
            cs = float(m.group(1))
        m = re.search(r"silence_end: ([\d.]+)", ln)
        if m and cs is not None:
            out.append((cs, float(m.group(1))))
            cs = None
    return out


def resolve(args) -> tuple[Path, Path, Path]:
    if args.slug:
        d = slug_dir(args.slug, getattr(args, "version", None))
        meta = json.loads((d / "render_metadata.json").read_text())
        source = Path(meta["inputs"][0])
        source = source if source.is_absolute() else ROOT / source
        plan = next((p for p in [d / "manual_edit_plan_v3.json", d / "manual_edit_plan.json",
                                 d / "edit_decisions.json"] if p.exists()))
        tr = next(d.glob("source_00*large-v3.transcript.json"), None) or next(d.glob("*transcript.json"))
        return source, plan, tr
    return (Path(args.source), Path(args.plan), Path(args.transcript))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug")
    ap.add_argument("--version", help="QA a specific version subfolder (e.g. v2). Default: latest.")
    ap.add_argument("--source"); ap.add_argument("--plan"); ap.add_argument("--transcript")
    ap.add_argument("--noise", type=float, default=-30.0)
    ap.add_argument("--min-sil", type=float, default=0.12)
    args = ap.parse_args()

    source, plan_path, tr_path = resolve(args)
    words = json.loads(Path(tr_path).read_text())["words"]
    chunks = json.loads(Path(plan_path).read_text())["chunks"]
    sil = silences(Path(source), args.noise, args.min_sil)

    def stop_after(t, hi):                       # где реально смолк звук после t, не дальше hi
        cand = [s for s, _ in sil if t - 0.20 <= s <= hi]
        return min(cand) if cand else None

    print(f"\nQA нарезки talking-head — {plan_path.name}  ({len(chunks)} кусков)")
    print(f"{'#':>2} {'старт-конец':>13} {'дл':>5} {'лид':>6} {'хвост':>6} {'статус'}")
    print("-" * 78)
    fails = warns = 0
    for n, c in enumerate(chunks):
        ws = [w for w in words if w["start"] < c["end"] and w["end"] > c["start"]]
        if not ws:
            print(f"{n:>2} {c['start']:6.2f}-{c['end']:6.2f} {c['duration']:5.2f}  — ПУСТОЙ КУСОК БЕЗ СЛОВ"); fails += 1; continue
        fw, lw = ws[0], ws[-1]
        nxt = chunks[n + 1] if n + 1 < len(chunks) else None
        hi = (nxt["start"] if nxt else c["end"] + 0.5)
        lead = fw["start"] - c["start"]                       # >0 воздух/бит, <0 подрез
        onset_clip = max(0.0, c["start"] - fw["start"])       # рез после начала первого слова
        # обрыв: слово, которое ещё звучит на месте реза c.end
        offset_clip = max((w["end"] - c["end"] for w in words
                           if w["start"] < c["end"] - 0.02 < w["end"]), default=0.0)
        stop = stop_after(lw["end"], hi)
        dead = (c["end"] - stop) if stop else (c["end"] - lw["end"])
        flags = []
        if onset_clip > CLIP_FAIL: flags.append(f"ПОДРЕЗ начала «{fw['word'].strip()}» {onset_clip:.2f}s"); fails += 1
        if offset_clip > CLIP_FAIL: flags.append(f"ОБРЫВ слова {offset_clip:.2f}s"); fails += 1
        if dead > TAIL_WARN: flags.append(f"ХВОСТ тишины {dead:.2f}s"); warns += 1
        status = "  ".join(flags) if flags else "ok"
        beat = " ◀бит" if lead > 0.4 else ""
        print(f"{n:>2} {c['start']:6.2f}-{c['end']:6.2f} {c['duration']:5.2f} {lead:+6.2f}{beat} {dead:+6.2f} {status}")
    print("-" * 78)
    verdict = "FAIL" if fails else ("WARN" if warns else "PASS")
    print(f"ИТОГ: {verdict}   (подрезы/обрывы: {fails}, длинные хвосты: {warns})\n")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
