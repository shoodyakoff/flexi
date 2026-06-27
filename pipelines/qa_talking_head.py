#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA для нарезки talking-head: тайминг резов по звуку + поиск дублей в речи.

Встроенный quality_report пайплайна смотрит только число слов в куске. Этот QA
ловит то, что реально портит ощущение от монтажа:

  • ХВОСТ   — тишина в конце фразы после того, как звук смолк (там видно, как
              человек отворачивает голову к тексту). Меряется по silencedetect.
  • ПОДРЕЗ  — рез попал на начало слова → слышно «обрубок» (онсет не успел).
  • ОБРЫВ   — рез прошёл ВНУТРИ слова, которое ещё звучало → «не договорил».
  • ЛИД     — сколько воздуха перед первым словом (для хука — намеренный «бит»).
  • ДУБЛЬ   — в финале осталась повторённая/перезапущенная фраза (фальстарт,
              который retake-стадия не вырезала). Это КОНТЕНТ, а не тайминг —
              сверяется по финальному timeline-транскрипту, не на глаз.

День обычно снят НЕСКОЛЬКИМИ клипами (человек останавливает камеру). Каждый
кусок проверяется против ЗВУКА И ТРАНСКРИПТА СВОЕГО клипа (по `source_index`),
иначе чужие куски ложно выглядят «пустыми». Поэтому при --slug всё берётся из
render_metadata.json (`inputs` + `subtitles.timeline_transcript`).

Запуск:
    python3 pipelines/qa_talking_head.py --slug d40
    python3 pipelines/qa_talking_head.py --slug d40 --version v2
    python3 pipelines/qa_talking_head.py --source S.mov --plan plan.json \
            --transcript words.json [--timeline-transcript timeline.json]

Коды выхода: 0 — без FAIL; 1 — есть FAIL (подрез/обрыв/пустой кусок/ДУБЛЬ).
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

# --- поиск дублей в финальной речи (рестарты/фальстарты) ---
# Настоящий рестарт стоит ВПРИТЫК и БЕЗ конца предложения между повторами
# (мысль оборвана на полуслове и сказана заново). Эхо слова через точку и
# анафора через целые предложения — НЕ дубли и отсекаются по этому признаку.
DUP_GAP_SEC = 6.0           # вторая попытка стартует не позже этого после первой
DUP_CUE_MIN_LEN = 4         # «слово-зацепка» заминки хотя бы такой длины (буквы)
DUP_CUE_MAX_WORDS = 3       # ... повторяется в пределах стольких слов
DUP_CUE_MAX_SEC = 3.0       # ... и не дальше стольких секунд
DUP_BIGRAM_MIN_MAXLEN = 4   # в повторённой биграмме хоть одно слово такой длины
DUP_MERGE_SEC = 1.2         # близкие находки склеиваем в одну


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


def _norm_word(raw: str) -> str:
    """Lowercase a word and strip punctuation, keeping Cyrillic/Latin/digits."""
    return re.sub(r"[^0-9a-zа-яё]", "", raw.lower())


def _ends_sentence(raw: str) -> bool:
    return bool(re.search(r"[.?!…]$", raw.strip()))


def find_duplicates(words: list[dict]) -> list[tuple[float, float, str]]:
    """Find repeated/restarted phrases left in the final cut.

    `words` is the word list from talking_head_timeline_transcript.json. Returns
    a merged list of (start, end, label) windows covering the FIRST (abandoned)
    attempt of each restart, so the caller can show what to cut.

    Two signals, both gated by "no sentence terminator between the repeats":
      A — a repeated adjacent bigram nearby (reordered/garbled restart, e.g.
          «на своём примере скажу … скажу на своём примере …»);
      B — a distinctive cue word repeated within a few words (stutter restart,
          e.g. «второе это ты второе то что …»).
    The terminator gate is what separates a real on-the-fly restart from a
    legit cross-sentence echo or anaphora.
    """
    toks = [(_norm_word(w["word"]), float(w["start"]), float(w["end"]), str(w["word"]).strip())
            for w in words]
    idx = [k for k, t in enumerate(toks) if t[0]]          # positions of real tokens
    where = {k: p for p, k in enumerate(idx)}

    def terminator_between(i: int, j: int) -> bool:
        return any(_ends_sentence(toks[idx[p]][3]) for p in range(where[i], where[j]))

    findings: list[tuple[float, float, str]] = []

    # Rule A — repeated adjacent bigram within the proximity window
    for a in range(len(idx) - 1):
        i, i2 = idx[a], idx[a + 1]
        bigram = (toks[i][0], toks[i2][0])
        if max(len(bigram[0]), len(bigram[1])) < DUP_BIGRAM_MIN_MAXLEN:
            continue
        for b in range(a + 2, len(idx) - 1):
            j = idx[b]
            if toks[j][1] - toks[i][2] > DUP_GAP_SEC:
                break
            if (toks[j][0], toks[idx[b + 1]][0]) == bigram and not terminator_between(i, j):
                findings.append((toks[i][1], toks[j][1], f"повтор «{bigram[0]} {bigram[1]}»"))
                break

    # Rule B — distinctive cue word repeated within a few words
    for a in range(len(idx)):
        i = idx[a]
        w = toks[i][0]
        if len(w) < DUP_CUE_MIN_LEN:
            continue
        for b in range(a + 1, min(a + 1 + DUP_CUE_MAX_WORDS, len(idx))):
            j = idx[b]
            if (toks[j][0] == w and toks[j][1] - toks[i][2] <= DUP_CUE_MAX_SEC
                    and not terminator_between(i, j)):
                findings.append((toks[i][1], toks[j][1], f"повтор слова «{w}»"))
                break

    findings.sort()
    merged: list[tuple[float, float, str]] = []
    for s, e, label in findings:
        if merged and s <= merged[-1][1] + DUP_MERGE_SEC:
            ps, pe, pl = merged[-1]
            merged[-1] = (ps, max(pe, e), pl + "; " + label)
        else:
            merged.append((s, e, label))
    return merged


def resolve(args) -> tuple[dict[int, Path], dict[int, Path], Path, Path | None]:
    """Return (sources, transcripts, plan_path, timeline_path).

    sources/transcripts are keyed by source_index so each clip is QA'd against
    its own audio + transcript. timeline_path (final-cut transcript for the dup
    check) may be None.
    """
    if args.slug:
        d = slug_dir(args.slug, getattr(args, "version", None))
        meta = json.loads((d / "render_metadata.json").read_text())
        sources: dict[int, Path] = {}
        transcripts: dict[int, Path] = {}
        for i, inp in enumerate(meta.get("inputs", [])):
            p = Path(inp)
            sources[i] = p if p.is_absolute() else ROOT / p
            tr = (next(d.glob(f"source_{i:02d}*large-v3.transcript.json"), None)
                  or next(d.glob(f"source_{i:02d}*transcript.json"), None))
            if tr:
                transcripts[i] = tr
        plan = next(p for p in [d / "manual_edit_plan_v3.json",
                                d / "manual_edit_plan.json",
                                d / "edit_decisions.json"] if p.exists())
        tl = d / "talking_head_timeline_transcript.json"
        return sources, transcripts, plan, (tl if tl.exists() else None)
    timeline = Path(args.timeline_transcript) if args.timeline_transcript else None
    return ({0: Path(args.source)}, {0: Path(args.transcript)}, Path(args.plan), timeline)


def qa_source_chunks(words: list[dict], chunks: list[tuple[int, dict]],
                     sil: list[tuple[float, float]]) -> tuple[list[str], int, int]:
    """Check cut timing for one clip's chunks. → (rows, fails, warns).

    `chunks` is a list of (global_index, chunk) so printed rows keep the plan's
    numbering. Word/silence times are in this clip's own timeline.
    """
    def stop_after(t, hi):                       # где реально смолк звук после t, не дальше hi
        cand = [s for s, _ in sil if t - 0.20 <= s <= hi]
        return min(cand) if cand else None

    rows: list[str] = []
    fails = warns = 0
    for n, c in chunks:
        start, end = float(c["start"]), float(c["end"])
        dur = float(c.get("duration", end - start))
        ws = [w for w in words if w["start"] < end and w["end"] > start]
        if not ws:
            rows.append(f"{n:>2} {start:6.2f}-{end:6.2f} {dur:5.2f}  — ПУСТОЙ КУСОК БЕЗ СЛОВ")
            fails += 1
            continue
        fw, lw = ws[0], ws[-1]
        hi = end + 0.5
        lead = fw["start"] - start                            # >0 воздух/бит, <0 подрез
        onset_clip = max(0.0, start - fw["start"])            # рез после начала первого слова
        offset_clip = max((w["end"] - end for w in words      # слово ещё звучит на месте реза
                           if w["start"] < end - 0.02 < w["end"]), default=0.0)
        stop = stop_after(lw["end"], hi)
        dead = (end - stop) if stop else (end - lw["end"])
        flags = []
        if onset_clip > CLIP_FAIL:
            flags.append(f"ПОДРЕЗ начала «{fw['word'].strip()}» {onset_clip:.2f}s"); fails += 1
        if offset_clip > CLIP_FAIL:
            flags.append(f"ОБРЫВ слова {offset_clip:.2f}s"); fails += 1
        if dead > TAIL_WARN:
            flags.append(f"ХВОСТ тишины {dead:.2f}s"); warns += 1
        status = "  ".join(flags) if flags else "ok"
        beat = " ◀бит" if lead > 0.4 else ""
        rows.append(f"{n:>2} {start:6.2f}-{end:6.2f} {dur:5.2f} {lead:+6.2f}{beat} {dead:+6.2f} {status}")
    return rows, fails, warns


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug")
    ap.add_argument("--version", help="QA a specific version subfolder (e.g. v2). Default: latest.")
    ap.add_argument("--source"); ap.add_argument("--plan"); ap.add_argument("--transcript")
    ap.add_argument("--timeline-transcript", dest="timeline_transcript",
                    help="Финальный timeline-транскрипт для проверки на дубли (explicit-режим).")
    ap.add_argument("--noise", type=float, default=-30.0)
    ap.add_argument("--min-sil", type=float, default=0.12)
    args = ap.parse_args()

    sources, transcripts, plan_path, timeline_path = resolve(args)
    chunks = json.loads(Path(plan_path).read_text())["chunks"]

    by_src: dict[int, list[tuple[int, dict]]] = {}
    for n, c in enumerate(chunks):
        by_src.setdefault(int(c.get("source_index", 0)), []).append((n, c))

    print(f"\nQA нарезки talking-head — {Path(plan_path).name}  "
          f"({len(chunks)} кусков, {len(by_src)} источн.)")
    print(f"{'#':>2} {'старт-конец':>13} {'дл':>5} {'лид':>6} {'хвост':>6} {'статус'}")

    fails = warns = 0
    for si in sorted(by_src):
        src = sources.get(si, sources.get(0))
        tr = transcripts.get(si, transcripts.get(0))
        label = src.name if src else f"source_index={si}"
        print("-" * 78)
        if not (src and tr and Path(src).exists() and Path(tr).exists()):
            print(f"  источник {si} ({label}): нет файла или транскрипта — пропуск проверки")
            fails += 1
            continue
        words = json.loads(Path(tr).read_text())["words"]
        sil = silences(Path(src), args.noise, args.min_sil)
        print(f"  источник {si}: {label}  ({len(by_src[si])} кусков)")
        rows, f, w = qa_source_chunks(words, by_src[si], sil)
        for r in rows:
            print(r)
        fails += f
        warns += w
    print("-" * 78)

    # --- проверка на дубли по финальному таймлайну (контент, не тайминг) ---
    dup_count = 0
    if timeline_path and Path(timeline_path).exists():
        tw = json.loads(Path(timeline_path).read_text())["words"]
        dups = find_duplicates(tw)
        if not dups:
            print("ДУБЛИ: не найдено ✓")
        else:
            print(f"ДУБЛИ: НАЙДЕНО {len(dups)} — вырезать вручную:")
            for s, e, lab in dups:
                text = "".join(w["word"] for w in tw if s <= w["start"] < e).strip()
                print(f"  ДУБЛЬ {s:6.2f}–{e:6.2f}  ({lab})")
                print(f"        «{text}»")
            dup_count = len(dups)
            fails += dup_count
    else:
        print("ДУБЛИ: проверка пропущена — нет timeline-транскрипта")
    print("-" * 78)

    verdict = "FAIL" if fails else ("WARN" if warns else "PASS")
    print(f"ИТОГ: {verdict}   (ошибок: {fails}, из них дублей: {dup_count}; предупреждений: {warns})\n")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
