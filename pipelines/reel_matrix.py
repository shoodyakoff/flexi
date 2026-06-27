#!/usr/bin/env python3
"""Reel matrix — modular combinatorial assembly of talking-head reels.

You shoot building blocks as separate clips and drop them all into ``raw/``:
  - N hooks, K tips (order-independent), M ctas.
The pipeline auto-detects each clip's role, cleans each block once, then mixes
hook x tip-order x cta into many unique videos.

Stages (CLI subcommands):
  ingest  raw/*.{mp4,mov} -> transcribe + classify role -> raw/manifest.json
  clean   each block -> silence-cut + HDR tonemap + vertical 1080x1920 (+ transcript)
  dryrun  enumerate hook x tip-perm x cta combos with durations (no render)
  build   assemble ONE combo (concat cleaned blocks + merge transcripts) -> render

This module keeps the *pure* logic (role classification, combo enumeration,
transcript merge, ffmpeg filter strings) import-friendly and unit-tested; the
heavy ffmpeg / Whisper / render steps reuse the existing pipeline tools.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from itertools import permutations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Allow `from src.X` / `from pipelines.X` when run directly as a script.
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from src.output_paths import versioned_dir

RAW_DIR = ROOT / "raw"
BRAND_DIR = ROOT / "assets/broll_brand"  # screen-demo clip library + demo_tags.json
W, H, FPS = 1080, 1920, 30
# Every product-demo insert must hold at least this long — no one-second flashes.
# If a block can't fit all its demos at this minimum, the shortest are dropped and
# the rest split the window evenly (each still >= MIN_DEMO_SEC).
MIN_DEMO_SEC = 2.0
# CTA product demo: holds ~3s after a brief talking-head lead. If the CTA block is
# too short to afford the full hold, the last frame is frozen for the remainder.
CTA_DEMO_SEC = 3.0
# Each block is trimmed to its last spoken word + this tail, dropping the dead air
# a clip leaves before the cut to the next block (no silent micro-lag at joins).
TAIL_PAD = 0.12
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}

# ---------------------------------------------------------------------------
# Role classification (pure)
# ---------------------------------------------------------------------------
Role = str  # "hook" | "tip" | "cta"

CTA_WORDS = (
    "ссылка в шапке", "ссылк", "шапк", "профил", "подпиш", "подписк", "сохрани",
    "сохраняй", "попробуй", "попробовать", "переходи", "перейди", "жми",
    "ставь лайк", "коммент", "забирай", "регистрир", "залетай в",
)
HOOK_WORDS = (
    "представ", "смотри", "знаеш", "секрет", "имба", "фишк", "никто не",
    "я нашёл", "я нашел", "за месяц", "за неделю", "жесть", "оффер", "расскажу",
    "показываю", "внимание", "стоп", "друг скинул", "почему", "как найти",
)
TIP_WORDS = (
    "сделай", "используй", "добавь", "проверь", "убери", "напиши", "зайди",
    "открой", "возьми", "начни", "перестань", "удали", "оптимизир", "адаптир",
    "первое", "второе", "третье", "действие", "способ", "совет",
)
FILENAME_HINTS = {
    "hook": ("hook", "хук", "_h", "h1", "h2", "h3"),
    "cta": ("cta", "концов", "финал", "_c", "c1", "c2", "c3", "outro"),
    "tip": ("tip", "совет", "способ", "_t", "t1", "t2", "t3", "body"),
}


def _norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def _count(text: str, words) -> int:
    return sum(text.count(w.replace("ё", "е")) for w in words)


def classify_clip_role(text: str, filename: str = "") -> tuple[Role, float, str]:
    """Guess whether a clip is a hook, tip, or cta from its words / filename.

    Returns (role, confidence 0..1, reason). Filename hints win when present;
    otherwise keyword scores decide, with CTA taking priority on ties because
    closing language ("ссылка в шапке") is the least ambiguous.
    """
    name = _norm(filename)
    for role, hints in FILENAME_HINTS.items():
        if any(h in name for h in hints):
            return role, 1.0, f"filename hint '{name}'"

    t = _norm(text)
    cta = _count(t, CTA_WORDS)
    hook = _count(t, HOOK_WORDS)
    tip = _count(t, TIP_WORDS)
    scores = {"cta": cta, "hook": hook, "tip": tip}
    best = max(scores, key=lambda k: (scores[k], k == "cta", k == "hook"))
    total = cta + hook + tip
    if total == 0:
        return "tip", 0.4, "no signal — defaulting to tip"
    confidence = round(min(0.95, 0.5 + scores[best] / (total + 1)), 2)
    return best, confidence, f"keywords cta={cta} hook={hook} tip={tip}"


# ---------------------------------------------------------------------------
# Combination enumeration (pure)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Combo:
    hook: str
    tips: tuple[str, ...]
    cta: str

    @property
    def id(self) -> str:
        return f"{self.hook}__{'+'.join(self.tips)}__{self.cta}"


def enumerate_combos(
    hooks: list[str],
    tips: list[str],
    ctas: list[str],
    *,
    permute_tips: bool = True,
) -> list[Combo]:
    """All hook x cta x tip-ordering combinations (or fixed tip order)."""
    orders = list(permutations(tips)) if permute_tips else [tuple(tips)]
    return [
        Combo(hook=h, tips=order, cta=c)
        for h in hooks
        for c in ctas
        for order in orders
    ]


# ---------------------------------------------------------------------------
# Transcript merge (pure-ish: depends only on the Transcript schema)
# ---------------------------------------------------------------------------
def merge_transcripts(transcripts: list, clip_durations: list[float] | None = None) -> "object":
    """Concatenate per-clip transcripts onto one gapless timeline (offsets).

    Offsets MUST advance by each clip's real *video* duration (pass clip_durations),
    not the transcript's last-word time — otherwise the merged duration is shorter
    than the concatenated video and the render truncates the tail (cut CTA).
    """
    from src.schemas import Transcript, Word

    words: list = []
    offset = 0.0
    for i, tr in enumerate(transcripts):
        clip_end = 0.0
        for w in tr.words:
            words.append(Word(word=w.word, start=round(w.start + offset, 3),
                              end=round(w.end + offset, 3)))
            clip_end = max(clip_end, w.end)
        if clip_durations is not None:
            offset += float(clip_durations[i])
        else:
            offset += max(float(tr.duration), clip_end)
    full = " ".join(w.word for w in words)
    return Transcript(words=words, full_text=full, duration=round(offset, 3))


# ---------------------------------------------------------------------------
# Clean filter (pure builder; ffmpeg runner uses it)
# ---------------------------------------------------------------------------
def build_clean_filter(segments: list[tuple[float, float]], *, hdr: bool) -> str:
    """ffmpeg -filter_complex that cuts to the speech segments, HDR-tonemaps if
    needed, crops to vertical 1080x1920, and concatenates (gapless, dense)."""
    tonemap = (
        "zscale=t=linear:npl=1000,format=gbrpf32le,"
        "zscale=p=bt709,tonemap=tonemap=mobius:desat=0,"
        "zscale=t=bt709:m=bt709:r=limited,format=yuv420p,"
        "eq=saturation=1.08:contrast=1.04:brightness=0.008,"
        if hdr else ""
    )
    parts, labels = [], []
    for i, (s, e) in enumerate(segments):
        parts.append(
            f"[0:v]trim={s:.3f}:{e:.3f},setpts=PTS-STARTPTS,fps={FPS},"
            f"{tonemap}"
            f"scale=w={W}:h={H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},setsar=1,format=yuv420p[v{i}];"
            f"[0:a]atrim={s:.3f}:{e:.3f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=48000:channel_layouts=stereo[a{i}]"
        )
        labels.append(f"[v{i}][a{i}]")
    return ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(segments)}:v=1:a=1[v][a]"


def speech_segments_from_silences(
    silences: list[tuple[float, float]], duration: float,
    *, head_pad: float = 0.08, tail_pad: float = 0.12, min_seg: float = 0.22,
) -> list[tuple[float, float]]:
    """Complement of silences -> padded, merged speech keep-ranges (pure)."""
    speech: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in silences:
        if s > cursor:
            speech.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration:
        speech.append((cursor, duration))
    out: list[list[float]] = []
    for s, e in speech:
        s2, e2 = max(0.0, s - head_pad), min(duration, e + tail_pad)
        if e2 - s2 < min_seg:
            continue
        if out and s2 <= out[-1][1] + 0.02:
            out[-1][1] = max(out[-1][1], e2)
        else:
            out.append([s2, e2])
    return [(round(a, 3), round(b, 3)) for a, b in out]


# ---------------------------------------------------------------------------
# I/O helpers (ffmpeg / ffprobe)
# ---------------------------------------------------------------------------
def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)


def probe_duration(path: Path) -> float:
    res = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(path)])
    try:
        return float(res.stdout.strip())
    except ValueError:
        return 0.0


def is_hdr(path: Path) -> bool:
    res = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=color_transfer", "-of", "default=nw=1:nk=1", str(path)])
    return res.stdout.strip() in {"arib-std-b67", "smpte2084"}


def detect_silences(path: Path, *, noise="-30dB", d=0.28) -> list[tuple[float, float]]:
    wav = Path("/tmp") / f"rm_{abs(hash(str(path))) % 10**8}.wav"
    _run(["ffmpeg", "-y", "-i", str(path), "-map", "0:a:0", "-ac", "1",
          "-ar", "16000", "-vn", str(wav)])
    res = _run(["ffmpeg", "-hide_banner", "-i", str(wav),
                "-af", f"silencedetect=noise={noise}:d={d}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", res.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", res.stderr)]
    return list(zip(starts, ends))


def clean_clip(src: Path, out_video: Path, out_transcript: Path, *, model="large-v3") -> None:
    """Silence-cut + HDR tonemap + vertical 1080x1920 + re-transcribe one block."""
    dur = probe_duration(src)
    segs = speech_segments_from_silences(detect_silences(src), dur, tail_pad=0.5)
    if not segs:
        segs = [(0.0, dur)]
    fc = build_clean_filter(segs, hdr=is_hdr(src))
    out_video.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", str(src), "-filter_complex", fc,
           "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
           "-crf", "18", "-r", str(FPS),
           "-x264-params", "colorprim=bt709:colormatrix=bt709:transfer=bt709",
           "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
           "-color_trc", "bt709", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", str(out_video)]
    res = _run(cmd)
    if res.returncode != 0:
        raise RuntimeError("clean ffmpeg failed\n" + res.stderr[-2000:])
    from src.transcribe import transcribe
    transcribe(out_video, out_transcript, language="ru", model_size=model)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
@dataclass
class Block:
    clip: str            # filename in raw/
    role: Role
    confidence: float
    reason: str
    text: str = ""
    key: str = ""        # short id, e.g. h1 / t2 / c3
    product: str = ""    # legacy single insert: product clip to show on this block
    product_from: str = ""  # legacy: delay that single demo until this word is spoken
    # Preferred: a SEQUENCE of screen-demo inserts within one block, each
    # {"clip": "demo_x.mp4", "from": "<trigger word>"}. Each insert plays from its
    # trigger word until the next insert's trigger (or the block's end). Lets a long
    # block walk through several product screens synced to what is being said.
    inserts: list = field(default_factory=list)
    # Optional per-block scene split: a list of {"from": "<trigger word>",
    # "format": "<format_id>"} that forces a subtitle/scene format from that spoken
    # word onward (until the next override's trigger or the block end). Lets one
    # block switch styles mid-sentence — e.g. keep the close-up plate on the first
    # phrase, then flip the rest to the dynamic hook style on the wide shot.
    scene_overrides: list = field(default_factory=list)
    # CTA blocks only: the screen-demo clip to slide up at the close (Scene 3). Set
    # it only on CTAs where a product demo fits (e.g. "попробовать бесплатно"); a
    # CTA without it stays a plain talking-head closer (Scene 1).
    cta_demo: str = ""


def load_manifest(raw: Path) -> list[Block]:
    data = json.loads((raw / "manifest.json").read_text(encoding="utf-8"))
    return [Block(**b) for b in data["blocks"]]


def save_manifest(raw: Path, blocks: list[Block]) -> None:
    (raw / "manifest.json").write_text(
        json.dumps({"blocks": [b.__dict__ for b in blocks]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def assign_keys(blocks: list[Block]) -> None:
    counters = {"hook": 0, "tip": 0, "cta": 0}
    prefix = {"hook": "h", "tip": "t", "cta": "c"}
    for b in sorted(blocks, key=lambda x: x.clip):
        counters[b.role] += 1
        b.key = f"{prefix[b.role]}{counters[b.role]}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def cmd_ingest(args: argparse.Namespace) -> int:
    """Clean (silence-cut + HDR tonemap + vertical) + transcribe + classify every
    raw clip, writing cleaned/ blocks and a reviewable manifest.json."""
    raw = args.raw
    out = args.out
    cleaned = out / "cleaned"
    cleaned.mkdir(parents=True, exist_ok=True)
    clips = sorted(p for p in raw.iterdir() if p.suffix.lower() in VIDEO_EXTS)
    if not clips:
        print(f"no clips in {raw}")
        return 1
    from src.schemas import Transcript
    blocks: list[Block] = []
    for clip in clips:
        cv = cleaned / f"{clip.stem}.mp4"
        ct = cleaned / f"{clip.stem}.transcript.json"
        if args.force or not (cv.exists() and ct.exists()):
            print(f"-> cleaning {clip.name}", flush=True)
            clean_clip(clip, cv, ct, model=args.model)
        tr = Transcript.model_validate_json(ct.read_text(encoding="utf-8"))
        role, conf, reason = classify_clip_role(tr.full_text, clip.name)
        blocks.append(Block(clip=clip.name, role=role, confidence=conf,
                            reason=reason, text=tr.full_text[:160]))
    assign_keys(blocks)
    save_manifest(out, blocks)
    print(f"\ncleaned + classified {len(blocks)} clips -> {out/'manifest.json'} (review roles!)")
    for b in sorted(blocks, key=lambda x: x.key):
        print(f"  {b.key:3s} {b.role:4s} {b.confidence:.2f}  {b.clip:20s} | {b.text[:54]}")
    return 0


def cmd_dryrun(args: argparse.Namespace) -> int:
    blocks = load_manifest(args.dir)
    hooks = [b.key for b in blocks if b.role == "hook"]
    tips = [b.key for b in blocks if b.role == "tip"]
    ctas = [b.key for b in blocks if b.role == "cta"]
    def _block_dur(b: Block) -> float:
        cleaned = args.dir / "cleaned" / f"{Path(b.clip).stem}.mp4"
        return probe_duration(cleaned if cleaned.exists() else args.dir / b.clip)

    durs = {b.key: _block_dur(b) for b in blocks}
    combos = enumerate_combos(hooks, tips, ctas, permute_tips=not args.fixed_order)
    print(f"blocks: {len(hooks)} hooks, {len(tips)} tips, {len(ctas)} ctas")
    print(f"combos: {len(combos)} unique videos "
          f"({len(hooks)} x {len(ctas)} x {'perm' if not args.fixed_order else 'fixed'} tips)\n")
    for c in combos[: args.limit]:
        seq = [c.hook, *c.tips, c.cta]
        total = sum(durs.get(k, 0.0) for k in seq)
        print(f"  {c.id:28s}  ~{total:4.1f}s  [{' '.join(seq)}]")
    if len(combos) > args.limit:
        print(f"  ... +{len(combos) - args.limit} more (use --limit)")
    return 0


def _concat_clips(clips: list[Path], out_video: Path, durs: list[float] | None = None) -> None:
    """Concatenate already-normalized (1080x1920/30fps/bt709/aac) clips.

    If ``durs`` is given, each clip is trimmed to ``durs[i]`` seconds first — used to
    drop trailing silence so there is no dead air before the cut to the next block."""
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]
    n = len(clips)
    if durs:
        parts = []
        for i in range(n):
            parts.append(f"[{i}:v]trim=0:{durs[i]:.3f},setpts=PTS-STARTPTS[v{i}]")
            parts.append(f"[{i}:a]atrim=0:{durs[i]:.3f},asetpts=PTS-STARTPTS[a{i}]")
        streams = "".join(f"[v{i}][a{i}]" for i in range(n))
        fc = ";".join(parts) + ";" + f"{streams}concat=n={n}:v=1:a=1[v][a]"
    else:
        streams = "".join(f"[{i}:v][{i}:a]" for i in range(n))
        fc = f"{streams}concat=n={n}:v=1:a=1[v][a]"
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(FPS),
           "-x264-params", "colorprim=bt709:colormatrix=bt709:transfer=bt709",
           "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
           "-color_trc", "bt709", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", str(out_video)]
    res = _run(cmd)
    if res.returncode != 0:
        raise RuntimeError("concat ffmpeg failed\n" + res.stderr[-2000:])


def _trim_end(clip: Path, dur: float, *, noise: str = "-35dB",
              min_sil: float = 0.3, pad: float = TAIL_PAD) -> float:
    """Where to cut the clip: just after the last speech, dropping only the trailing
    silence. Measured with silencedetect (real audio), so a word is never clipped —
    unlike trusting Whisper word-end times, which land early and cut the last word."""
    res = _run(["ffmpeg", "-hide_banner", "-i", str(clip),
                "-af", f"silencedetect=n={noise}:d={min_sil}", "-f", "null", "-"])
    out = res.stderr or ""
    starts = re.findall(r"silence_start:\s*(-?[0-9.]+)", out)
    ends = re.findall(r"silence_end:\s*([0-9.]+)", out)
    trailing = None
    if len(starts) > len(ends):              # silence runs to EOF (no closing end)
        trailing = float(starts[-1])
    elif ends and starts and float(ends[-1]) >= dur - 0.05:  # closing end at EOF
        trailing = float(starts[-1])
    if trailing is not None and trailing > 0.2:
        return min(dur, round(trailing + pad, 3))
    return dur


def _resolve_audio(folder: Path, placeholder: Path, *, seconds: float) -> Path:
    """First real audio file in *folder*, else a generated silent placeholder.

    The ref-style renderer needs a music + sfx input; its hard-coded defaults
    (provocative.mp3 / swoosh.mp3) may not exist. So we use whatever the user
    dropped in assets/music | assets/sounds, and fall back to silence so a sample
    still renders. Drop a real track and it is picked up automatically.
    """
    for ext in (".mp3", ".m4a", ".wav", ".aac"):
        hits = sorted(folder.glob(f"*{ext}"))
        if hits:
            return hits[0]
    if not placeholder.exists():
        placeholder.parent.mkdir(parents=True, exist_ok=True)
        _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
              "-t", f"{seconds:.2f}", "-c:a", "libmp3lame", "-b:a", "128k", str(placeholder)])
    return placeholder


def cmd_build(args: argparse.Namespace) -> int:
    """Assemble ONE combo: concat cleaned blocks + merge transcripts -> ref-style render."""
    from src.schemas import Transcript

    d = args.dir
    cleaned = d / "cleaned"
    blocks = {b.key: b for b in load_manifest(d)}
    keys = [k for k in re.split(r"[,+ ]+", args.combo.strip()) if k]
    clips, trs = [], []
    for k in keys:
        stem = Path(blocks[k].clip).stem
        clips.append(cleaned / f"{stem}.mp4")
        trs.append(Transcript.model_validate_json(
            (cleaned / f"{stem}.transcript.json").read_text(encoding="utf-8")))
    # Trim each block's trailing silence (measured from the real audio) so no dead air
    # is left before the cut to the next block — without ever clipping the last word.
    clip_durs = [_trim_end(c, probe_duration(c)) for c in clips]
    tag = "_".join(keys)
    # All working artifacts (assembly, render sidecars: base/ass/plan/diagnostics)
    # live in work/. Only the finished mp4 is delivered to a clean final/ folder.
    work = d / "work"
    work.mkdir(parents=True, exist_ok=True)
    assembled = work / f"asm_{tag}.mp4"
    merged_t = work / f"asm_{tag}.transcript.json"
    _concat_clips(clips, assembled, clip_durs)
    # Merged transcript object — its duration may grow if the CTA needs a tail
    # freeze, so we write it after the override loop below.
    mt = merge_transcripts(trs, clip_durs)
    # Per-block product inserts: place an override per demo on each block. Tip
    # blocks are forced to the blue demo format; cta blocks keep their format.
    # A block may carry a SEQUENCE (b.inserts) — each demo plays from its trigger
    # word until the next trigger (or block end) — or the legacy single product.
    def _word_offset(tr, trigger: str) -> float:
        kw = (trigger or "").lower().replace("ё", "е")
        if kw:
            for w in tr.words:
                if kw in w.word.lower().replace("ё", "е"):
                    return float(w.start)
        return 0.0

    overrides: list[dict] = []
    start = 0.0
    for k, dur, tr in zip(keys, clip_durs, trs):
        b = blocks[k]
        fmt = "format_4_blue_demo" if b.role == "tip" else None
        seq = list(b.inserts) if b.inserts else (
            [{"clip": b.product, "from": b.product_from}] if b.product else [])
        # Resolve each insert's absolute start (its trigger word), then enforce the
        # 2-second minimum: drop the shortest demos that don't fit and split the
        # window [first trigger .. block end] evenly among the survivors.
        resolved = sorted(
            ((start + _word_offset(tr, ins.get("from", "")), ins["clip"]) for ins in seq),
            key=lambda x: x[0],
        )
        if resolved:
            block_end = start + dur
            win_start = resolved[0][0]
            avail = max(0.0, block_end - win_start)
            keep = max(1, min(len(resolved), int(avail // MIN_DEMO_SEC)))
            survivors = resolved
            if keep < len(resolved):
                natural = [
                    (resolved[i + 1][0] if i + 1 < len(resolved) else block_end) - resolved[i][0]
                    for i in range(len(resolved))
                ]
                drop = set(sorted(range(len(resolved)), key=lambda i: natural[i])[: len(resolved) - keep])
                survivors = [r for i, r in enumerate(resolved) if i not in drop]
            seg_len = avail / len(survivors)
            for j, (_, clip) in enumerate(survivors):
                demo_start = win_start + j * seg_len
                demo_end = block_end if j + 1 == len(survivors) else win_start + (j + 1) * seg_len
                overrides.append({
                    "start": round(demo_start, 3),
                    "end": round(demo_end, 3),
                    "format": fmt,
                    "clip": clip,
                })
        # Per-block scene split: force a subtitle/scene format from a trigger word
        # onward (each runs until the next override's trigger, or the block end).
        so_seq = sorted(
            ((start + _word_offset(tr, so.get("from", "")), so["format"])
             for so in (b.scene_overrides or [])),
            key=lambda x: x[0],
        )
        for i, (so_start, so_fmt) in enumerate(so_seq):
            so_end = so_seq[i + 1][0] if i + 1 < len(so_seq) else start + dur
            overrides.append({
                "start": round(so_start, 3),
                "end": round(so_end, 3),
                "format": so_fmt,
            })
        # Hook: always Scene 1 (dynamic word-stickers). The heuristic director can
        # mis-label a longer hook as the close-up plate, so pin the whole block.
        if b.role == "hook":
            overrides.append({
                "start": round(start, 3),
                "end": round(start + dur, 3),
                "format": "format_1_hook_metal",
            })
        # CTA: the product demo fills the closing seconds, and the video ends exactly
        # when the speech stops — NO frozen tail. The demo slides up CTA_DEMO_SEC
        # before the block ends (Scene 3, head + lower demo), clamped to the block
        # start so it never eats into the previous block. If the CTA replica is
        # shorter than CTA_DEMO_SEC, the demo simply fills the whole CTA.
        if b.role == "cta" and dur > 1.0:
            cta_clip = b.cta_demo or "demo_landing.mp4"
            cta_end = start + dur
            demo_start = max(start, round(cta_end - CTA_DEMO_SEC, 3))
            if demo_start > start + 0.05:  # room for a brief talking-head lead
                overrides.append({
                    "start": round(start, 3),
                    "end": demo_start,
                    "format": "format_1_hook_metal",
                })
            overrides.append({
                "start": demo_start,
                "end": round(cta_end, 3),
                "format": "format_5_lower_demo_cta",
                "clip": cta_clip,
            })
        start += dur
    merged_t.write_text(mt.model_dump_json(indent=2), encoding="utf-8")
    render_out = work / f"{tag}.mp4"  # sidecars land next to this, inside work/
    render = [sys.executable, str(ROOT / "pipelines/render_ref_style_directed.py"),
              "--source", str(assembled), "--transcript", str(merged_t),
              "--skip-clean-prelayer", "--no-version", "--output", str(render_out)]
    # Make the whole screen-demo library available to the renderer so (a) forced
    # per-block inserts (override "clip") resolve to a real file, and (b) any
    # non-forced product beat is picked by keyword from demo_tags.json. The render
    # only globs nothing on its own (default_product_paths is stale), so we pass it.
    demo_clips = sorted(BRAND_DIR.glob("demo_*.mp4"))
    for clip in demo_clips:
        render += ["--product", str(clip)]
    demo_tags = BRAND_DIR / "demo_tags.json"
    if demo_tags.exists():
        render += ["--demo-tags", str(demo_tags)]
    # Music + sfx: real files if the user dropped any, else a silent placeholder
    # so the sample still renders (swap real music in later via assets/music).
    total_dur = sum(clip_durs)
    music = _resolve_audio(ROOT / "assets/music", work / "_placeholder_music.mp3",
                           seconds=total_dur + 5)
    sfx = _resolve_audio(ROOT / "assets/sounds", work / "_placeholder_sfx.mp3", seconds=0.5)
    render += ["--music", str(music), "--sfx", str(sfx)]
    # Steady product demos: never flash to the live head mid-demo (each demo holds
    # its 2-3 seconds — see MIN_DEMO_SEC and the override builder above).
    render += ["--no-head-cutaways"]
    # Replace the yellow framed-face backdrop with the user's looping bg video.
    # Prefer the compact pre-trimmed copy over the heavy raw background.mp4.
    bg_video = next(
        (p for p in (ROOT / "assets/backgrounds/bg_main.mp4", ROOT / "background.mp4") if p.exists()),
        None,
    )
    if bg_video is not None:
        render += ["--bg-video", str(bg_video)]
    # No global cold-open: the video opens straight on the hook (Scene 1). The demo
    # teaser belongs inside a fragment, not as a pre-hook intro.
    if overrides:
        ov_path = work / f"asm_{tag}.product_overrides.json"
        ov_path.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")
        render += ["--product-overrides", str(ov_path), "--strict-product-overrides"]
    import os
    env = {**os.environ, "PYTHONPATH": f"{ROOT}/src:{ROOT}", "PYTHONDONTWRITEBYTECODE": "1"}
    res = subprocess.run(render, cwd=ROOT, text=True, capture_output=True, env=env)
    if res.returncode != 0:
        raise RuntimeError("render failed\n" + res.stdout[-1500:] + res.stderr[-1500:])
    # Deliver ONLY the finished video into the clean production folder. Each
    # re-render of the same combo becomes a new version inside final/<tag>/.
    final = args.output or (versioned_dir(d / "final" / tag) / f"{tag}.mp4")
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(render_out, final)
    print(final)
    return 0


# ---------------------------------------------------------------------------
# Publish: collect latest renders into one flat folder + a status tracker
# ---------------------------------------------------------------------------
# The intended matrix for this project: 3 hooks x 2 main-part orders (t1 fixed in
# the middle, t2/t3 swap) x 3 CTAs = 18 unique videos.
MATRIX_HOOKS = ["h1", "h2", "h3"]
MATRIX_ORDERS = [["t2", "t1", "t3"], ["t3", "t1", "t2"]]
MATRIX_CTAS = ["c1", "c2", "c3"]


def matrix_combos() -> list[str]:
    return [f"{h}_{'_'.join(o)}_{c}"
            for h in MATRIX_HOOKS for o in MATRIX_ORDERS for c in MATRIX_CTAS]


def _latest_version(combo_dir: Path) -> Path | None:
    vs = [(int(p.name[1:]), p) for p in combo_dir.glob("v*")
          if p.is_dir() and p.name[1:].isdigit()]
    return max(vs)[1] if vs else None


def _read_published(md_path: Path) -> set[str]:
    """Keep the user's [x] «выложено» ticks across re-publishes."""
    if not md_path.exists():
        return set()
    pub: set[str] = set()
    for line in md_path.read_text(encoding="utf-8").splitlines():
        if "[x]" in line.lower():
            m = re.search(r"(h\d_t\d_t\d_t\d_c\d)", line)
            if m:
                pub.add(m.group(1))
    return pub


def _write_matrix_doc(md_path: Path, combos: list[str],
                      generated: dict[str, str], published: set[str]) -> None:
    n_gen = sum(1 for t in combos if t in generated)
    n_pub = sum(1 for t in combos if t in published)
    lines = [
        "# Матрица сборки — статус",
        "",
        f"**Готово видео: {n_gen} / {len(combos)}** · **выложено: {n_pub} / {len(combos)}**",
        "",
        "Все собранные ролики (последние версии) лежат плоско в папке **`готовые/`** —",
        "бери файл `<комбо>.mp4` и выкладывай, по подпапкам ходить не нужно.",
        "",
        "Порядок основы: **A** = анализ→ХАХА→адаптация (`t2,t1,t3`), "
        "**B** = адаптация→ХАХА→анализ (`t3,t1,t2`).",
        "",
        "Как отмечать выложенное: поставь `x` в скобках в колонке «Выложено» "
        "(`[ ]` → `[x]`) — это сохранится при следующей пересборке.",
        "",
        "| № | Комбо | Хук | Осн. | CTA | Сгенерировано | Выложено |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, tag in enumerate(combos, 1):
        p = tag.split("_")  # h1 t2 t1 t3 c1
        order = "A" if p[1] == "t2" else "B"
        gen = f"✅ {generated[tag]}" if tag in generated else "—"
        pub = "[x]" if tag in published else "[ ]"
        lines.append(f"| {i} | `{tag}` | {p[0]} | {order} | {p[-1]} | {gen} | {pub} |")
    lines.append("")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_publish(args: argparse.Namespace) -> int:
    """Copy each combo's latest render into a flat `готовые/` folder and refresh the
    status tracker (preserving which videos you've already marked as posted)."""
    d = args.dir
    final = d / "final"
    ready = d / "готовые"
    ready.mkdir(parents=True, exist_ok=True)
    combos = matrix_combos()
    generated: dict[str, str] = {}
    for tag in combos:
        cdir = final / tag
        if not cdir.is_dir():
            continue
        latest = _latest_version(cdir)
        if latest is None:
            continue
        src = latest / f"{tag}.mp4"
        if src.exists():
            shutil.copy2(src, ready / f"{tag}.mp4")
            generated[tag] = latest.name
    published = _read_published(d / "МАТРИЦА.md") & set(combos)
    _write_matrix_doc(d / "МАТРИЦА.md", combos, generated, published)
    print(f"готовые: {ready}  ({len(generated)}/{len(combos)} собрано, "
          f"{len(published)} выложено)")
    print(f"трекер:  {d / 'МАТРИЦА.md'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="clean + transcribe + classify clips -> cleaned/ + manifest")
    p.add_argument("--raw", type=Path, default=RAW_DIR, help="folder of raw clips")
    p.add_argument("--out", type=Path, default=ROOT / "output" / "matrix",
                   help="where cleaned/ blocks and manifest.json are written")
    p.add_argument("--model", default="large-v3")
    p.add_argument("--force", action="store_true", help="re-clean even if a block already exists")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("dryrun", help="list combos + durations (no render)")
    p.add_argument("--dir", type=Path, default=ROOT / "output" / "matrix",
                   help="folder with manifest.json + cleaned/ blocks")
    p.add_argument("--limit", type=int, default=60)
    p.add_argument("--fixed-order", action="store_true", help="keep tip order, no permutations")
    p.set_defaults(func=cmd_dryrun)

    p = sub.add_parser("build", help="assemble + render ONE combo by keys, e.g. --combo h1,t2,t1,c3")
    p.add_argument("--dir", type=Path, default=ROOT / "output" / "matrix",
                   help="folder with manifest.json + cleaned/ blocks")
    p.add_argument("--combo", required=True, help="ordered block keys, e.g. 'h1,t3,t1,t2,c2'")
    p.add_argument("--output", type=Path, default=None)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("publish", help="copy latest renders into a flat готовые/ folder + refresh МАТРИЦА.md status")
    p.add_argument("--dir", type=Path, default=ROOT / "output" / "matrix",
                   help="folder with final/ renders")
    p.set_defaults(func=cmd_publish)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
