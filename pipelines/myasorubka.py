#!/usr/bin/env python3
"""Мясорубка — lean hook×demo combinatorial assembly for reaction reels.

One finished reel = a ready, already-subtitled HOOK clip glued in front of a
reaction "demo" BODY, with burned Russian subtitles written onto the body's
narration (Whisper → corrections → ASS → libass burn).

Lean combiner for the МясорубкаХукОснова project — it does NOT route through the
ref-style director. The body plays as itself, just normalized + subtitled.

Two commands:
  build     assemble ONE hook+demo reel (with optional `--seed` uniqueness).
  uniquify  apply the uniqueness layer to an ALREADY-FINISHED reel (e.g. Reel
            Matrix `hX_tY_tZ_cW.mp4` outputs) so segment-sharing variants differ.

Uniqueness (`--seed N`): when many reels share a hook/segment, IG can flag the
identical parts as duplicates. The seed applies, per output, a tiny ≤1° rotation
(the geometry lever perceptual hashes react to — opposite directions across reels
that share a hook), a slow whole-clip zoom + hue nudge (per-frame change, no
warping of burned text), faint grain, a varied GOP, and strips all metadata.
Hard rule: audio is never pitch/speed-shifted and the frame is never mirrored
(would break subtitles).

Usage:
    python pipelines/myasorubka.py build --seed 3 \
        --hook ".../Хуки/13.mp4" --demo ".../Демо/1.mp4" --out ".../Готовое/13__1.mp4"
    python pipelines/myasorubka.py uniquify --seed 5 \
        --in ".../Готовые/h1_t2_t1_t3_c1.mp4" --out ".../уникальные/h1_t2_t1_t3_c1.mp4"
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import yaml  # noqa: E402

from src.schemas import Config, Transcript, Word  # noqa: E402
from src.subtitles import (  # noqa: E402
    apply_word_corrections,
    build_corrections_map,
    transcript_to_ass,
)
from src.transcribe import transcribe  # noqa: E402

W, H, FPS = 1080, 1920, 30

# HDR (HLG/PQ) → SDR bt709 tonemap, prepended to the scale chain when needed.
TONEMAP = (
    "zscale=t=linear:npl=1000,format=gbrpf32le,"
    "zscale=p=bt709,tonemap=tonemap=mobius:desat=0,"
    "zscale=t=bt709:m=bt709:r=limited,format=yuv420p,"
)

# Per-seed zoom anchors — a slow push-in toward a different point per variant, so
# the SAME hook/segment gets a visually distinct motion trajectory in each reel.
_ANCHORS = [(0.5, 0.5), (0.36, 0.46), (0.64, 0.46), (0.5, 0.36), (0.46, 0.62)]


def _hook_motion_vf(seed: int) -> str:
    """Gentle per-seed animated zoom (+ tiny hue) for the shared hook."""
    z = 1.030 + 0.007 * (seed % 4)          # 1.030 .. 1.051 final zoom
    ax, ay = _ANCHORS[seed % len(_ANCHORS)]
    inc = 0.00018 + 0.00002 * (seed % 3)    # climb rate varies a touch
    hue = 2 + (seed % 3)                     # 2 .. 4 degrees, invisible to eye
    return (
        f"zoompan=z='min(zoom+{inc:.5f},{z:.3f})':d=1:"
        f"x='(iw-iw/zoom)*{ax}':y='(ih-ih/zoom)*{ay}':s={W}x{H}:fps={FPS},"
        f"hue=h={hue}"
    )


def load_config() -> Config:
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    return Config(**raw)


def _run(cmd: list) -> subprocess.CompletedProcess:
    r = subprocess.run([str(c) for c in cmd], text=True, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg/ffprobe failed:\n" + r.stderr[-3000:])
    return r


def _color_transfer(src: Path) -> str:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=color_transfer", "-of", "default=nw=1:nk=1", str(src)],
        text=True, capture_output=True,
    )
    return r.stdout.strip()


def _is_hdr(src: Path) -> bool:
    return _color_transfer(src) in {"arib-std-b67", "smpte2084"}


def probe_duration(src: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(src)],
        text=True, capture_output=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def normalize(src: Path, out: Path, *, ass: Path | None = None,
              fonts_dir: Path | None = None, crf: int = 18,
              motion_seed: int | None = None) -> None:
    """Vertical 1080x1920@30, HDR→SDR tonemap if needed, loudnorm.

    ``ass`` burns subtitles after scale (body). ``motion_seed`` adds the per-seed
    gentle zoom/hue (hook). They are never combined on the same clip.
    """
    vf = ""
    if _is_hdr(src):
        vf += TONEMAP
    vf += f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},"
    if motion_seed is not None:
        vf += _hook_motion_vf(motion_seed) + ","
    if ass is not None:
        vf += f"subtitles={ass}:fontsdir={fonts_dir},"
    vf += "format=yuv420p"
    af = "aformat=sample_rates=48000:channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11"
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", src, "-vf", vf, "-af", af,
        "-c:v", "libx264", "-preset", "medium", "-crf", crf, "-r", FPS,
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-color_range", "tv", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", out,
    ])


def concat(first: Path, second: Path, out: Path, *, crf: int = 20,
           grain_seed: int | None = None) -> None:
    """Glue two normalized clips. ``grain_seed`` adds faint per-seed grain; all
    source metadata is stripped either way (cheap dedup insurance)."""
    if grain_seed is not None:
        strength = 3 + (grain_seed % 3)  # 3..5, barely visible
        fc = ("[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vc][a];"
              f"[vc]noise=alls={strength}:allf=t+u[v]")
    else:
        fc = "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]"
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", first, "-i", second, "-filter_complex", fc,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", crf, "-r", FPS,
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-color_range", "tv", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-map_metadata", "-1", "-movflags", "+faststart", out,
    ])


def uniquify(src: Path, out: Path, *, seed: int, crf: int = 20) -> None:
    """Apply the uniqueness layer to a finished reel (whole clip).

    A per-seed micro-ROTATION (the geometry lever perceptual hashes actually
    react to — constant across the clip, so it diverges from frame 1, which is
    what a shared HOOK needs), plus a slow zoom (per-frame change, no warping of
    burned text), per-seed hue + faint grain, a varied GOP, and a metadata strip.
    The rotation is ≤1° and runs inside an over-scaled frame, so no black corners
    appear and burned text never visibly tilts. Audio is only re-encoded — never
    pitch/speed-shifted. Frame is never mirrored. Duration is preserved.
    """
    dur = probe_duration(src)
    frames = max(1, round(dur * FPS))
    z = 1.020 + 0.006 * (seed % 4)          # 1.020 .. 1.038 total zoom (subtle)
    ax, ay = _ANCHORS[seed % len(_ANCHORS)]
    inc = (z - 1.0) / frames                 # reach z by the last frame
    hue = 2 + (seed % 3)                     # 2 .. 4 degrees
    grain = 3 + (seed % 3)                   # 3 .. 5
    gop = 16 + (seed % 7)                    # vary keyframe interval
    # Per-seed tilt: 0.60 .. 0.96 deg, sign alternates by parity. Different reels
    # that share a hook get OPPOSITE-direction tilts -> the shared opening reads
    # as geometrically distinct to a perceptual hash, frame 1 onward.
    deg = (0.60 + 0.12 * (seed % 4)) * (1 if seed % 2 == 0 else -1)
    rad = deg * 3.14159265 / 180.0
    os = 1.07                                # over-scale headroom so the tilt never exposes corners
    ow, oh = round(W * os), round(H * os)
    ow -= ow % 2; oh -= oh % 2
    vf = ""
    if _is_hdr(src):
        vf += TONEMAP
    vf += (
        f"scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh},setsar=1,fps={FPS},"
        f"rotate={rad:.6f}:c=black,crop={W}:{H},"
        f"zoompan=z='min(zoom+{inc:.7f},{z:.3f})':d=1:"
        f"x='(iw-iw/zoom)*{ax}':y='(ih-ih/zoom)*{ay}':s={W}x{H}:fps={FPS},"
        f"hue=h={hue},noise=alls={grain}:allf=t+u,format=yuv420p"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", src, "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", crf, "-r", FPS, "-g", gop,
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-color_range", "tv", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-map_metadata", "-1", "-movflags", "+faststart", out,
    ])


def correct_transcript(tr: Transcript, corrections: dict) -> Transcript:
    m = build_corrections_map(corrections)
    words = [Word(word=apply_word_corrections(w.word, m), start=w.start, end=w.end)
             for w in tr.words]
    return Transcript(words=words, full_text=" ".join(w.word for w in words),
                      duration=tr.duration)


def build_one(hook: Path, demo: Path, out: Path, *, style_id: str = "editorial_pop",
              caption_bottom_pad: int = 300, seed: int | None = None,
              force: bool = False) -> Path:
    cfg = load_config()
    style = cfg.subtitle_styles[style_id]
    # Body captions sit LOWER than the shared default for this project — the lower
    # frame of a reaction body is empty desktop, not b-roll, so we drop the safe
    # box. Local override only; the global subtitle_safe_box is untouched.
    safe_box = cfg.subtitle_safe_box.model_copy(update={"bottom_padding_px": caption_bottom_pad})
    fonts_dir = (ROOT / cfg.fonts.directory).resolve()
    work = ROOT / "output" / "myasorubka" / "work"
    work.mkdir(parents=True, exist_ok=True)
    tag = f"{hook.stem}__{demo.stem}"

    # Body + burned subtitles are seed-INDEPENDENT — build once, cache, reuse.
    body_sub = work / f"{tag}.body_sub.mp4"
    if force or not body_sub.exists():
        print(f"[body] transcribe + subtitle: {demo.name}", flush=True)
        tr = transcribe(demo, work / f"{tag}.body.transcript.json",
                        language="ru", model_size=cfg.whisper.transcribe_model)
        tr = correct_transcript(tr, cfg.subtitle_corrections)
        ass = work / f"{tag}.body.ass"
        transcript_to_ass(tr, style, ass, safe_box=safe_box, cfg=cfg, section="body")
        normalize(demo, body_sub, ass=ass, fonts_dir=fonts_dir, crf=cfg.video.crf_intermediate)
    else:
        print(f"[body] reuse cached {body_sub.name}", flush=True)

    # Hook: per-seed gentle motion so the SHARED opening differs across variants.
    suffix = f".s{seed}" if seed is not None else ""
    hook_norm = work / f"{tag}.hook{suffix}.mp4"
    print(f"[hook] normalize{f' + motion (seed {seed})' if seed is not None else ''}", flush=True)
    normalize(hook, hook_norm, crf=cfg.video.crf_intermediate, motion_seed=seed)

    print(f"[final] concat{f' + grain/strip (seed {seed})' if seed is not None else ''}", flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    concat(hook_norm, body_sub, out, crf=cfg.video.crf, grain_seed=seed)
    print(f"DONE: {out}", flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="assemble ONE hook+demo reel")
    b.add_argument("--hook", required=True, type=Path)
    b.add_argument("--demo", required=True, type=Path)
    b.add_argument("--out", required=True, type=Path)
    b.add_argument("--style", default="editorial_pop")
    b.add_argument("--caption-bottom-pad", type=int, default=300,
                   help="smaller = body captions sit lower (safe-box bottom padding, px)")
    b.add_argument("--seed", type=int, default=None,
                   help="uniqueness seed: per-variant hook motion + grain + metadata strip")
    b.add_argument("--force", action="store_true", help="rebuild the cached body too")

    u = sub.add_parser("uniquify", help="apply the uniqueness layer to a finished reel")
    u.add_argument("--in", dest="src", required=True, type=Path)
    u.add_argument("--out", required=True, type=Path)
    u.add_argument("--seed", type=int, required=True)

    args = ap.parse_args()
    if args.cmd == "build":
        build_one(args.hook, args.demo, args.out, style_id=args.style,
                  caption_bottom_pad=args.caption_bottom_pad, seed=args.seed, force=args.force)
    elif args.cmd == "uniquify":
        uniquify(args.src, args.out, seed=args.seed)
        print(f"DONE: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
