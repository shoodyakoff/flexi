"""Build orchestrator for the shnurok route.

Ties together the building blocks from Tasks 1-9a (style config, structure
inference, transcription, title-plan, title/word-sub ASS generation, graphic
cutout, hook composite, loudness-matched audio mix) into a full reel per
style and writes it into the shared versioned-output layout
(`src/output_paths.py`).

Auto-mode design: see `.superpowers/sdd/2026-08-07-shnurok-pipeline/` task
9b brief. Kept intentionally simple (see "known simplifications" in the
module-level docstrings below and the task report) — this is a skeleton the
operating agent overrides per-shot via direct calls to the building blocks
(e.g. `compose_hook` with text-behind-subject, a specific fly-in keyword
window) per the eventual playbook (Task 12).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml
from rich.console import Console

from src.output_paths import update_latest, versioned_dir
from src.shnurok.structure import inventory, order_broll, split_hook_cta
from src.shnurok.style import load_style
from src.shnurok.titleplan import cta_screens_from_words, hook_lines_from_words
from src.shnurok.titles import cta_titles_ass, hook_titles_ass
from src.shnurok.word_subs import word_subs_ass
from src.shnurok.cutout import cutout_graphic
from src.shnurok.hook_compose import compose_hook
from src.shnurok.audio_mix import mix_voice_music
from src.shnurok.render_body import render_body
from src.shnurok.render_cta import render_cta

console = Console()

FONTS_DIR = "assets/fonts"
DEFAULT_MUSIC = Path("assets/music/provocative.mp3")

# Filenames that look like native camera output (IMG_1234.MOV, MVI_0001.MOV,
# DJI_0001.MP4, GOPR0001.MP4, ...). Used to prefer real footage over a
# downloaded reference/inspiration clip that happens to carry an audio
# stream too (see `_pick_talking_head` / `_broll_fallback` below).
_CAMERA_NAME_RE = re.compile(r"^(img|mvi|dji|gopr?o?)[_-]?\d", re.IGNORECASE)


def _camera_native(paths: list[Path]) -> list[Path]:
    matches = [p for p in paths if _CAMERA_NAME_RE.match(p.stem)]
    return matches or list(paths)


def _pick_talking_head(inv: dict) -> Path:
    """Best-effort pick of the real talking-head clip out of
    `inv["talking_head"]`. This is a FALLBACK for unattended/quick use only
    — the operating agent should pass `talking_head=...` explicitly whenever
    it knows the folder's structure (it always does, since it just sorted
    `raw/` into it).

    `inventory()` classifies ANY video with an audio stream as
    "talking_head" (see structure.py) — a folder can contain several such
    videos (e.g. product/demo clips shot with ambient mic noise, or a
    downloaded reference reel used only as visual inspiration, never as
    source footage). We can't afford to transcribe every candidate just to
    find the one real take, so we prefer camera-native filenames
    (IMG_####/MVI_####/DJI_####/...) over anything else (a downloaded
    reel's filename never matches that convention), then take the
    alphabetically-first match. When that still leaves 2+ candidates, this
    is a genuine guess (in this fixture it only resolves correctly because
    IMG_3968 sorts before IMG_9575 — an accident of naming, not a
    guarantee) — warn loudly instead of pretending it's reliable.
    """
    candidates = inv.get("talking_head") or []
    if not candidates:
        raise ValueError(
            "build_shnurok: no talking-head clip found in inventory (pass talking_head=... explicitly)"
        )
    picked = _camera_native(candidates)
    chosen = picked[0]
    if len(picked) > 1:
        console.log(
            f"[yellow]⚠[/yellow] build_shnurok: {len(picked)} talking-head candidates "
            f"({', '.join(p.name for p in picked)}) — guessed '{chosen.name}' (camera-native "
            "filename, alphabetically first). If wrong, pass talking_head=<path> explicitly."
        )
    return chosen


def _broll_fallback(inv: dict, th: Path) -> list[Path]:
    """B-roll pool when `inventory()` found none.

    `inventory()` only buckets a video as "broll" when it has NO audio
    stream at all. In practice, handheld product/demo footage almost always
    carries an ambient audio track, so it lands in "talking_head" instead —
    leaving `inv["broll"]` empty even though real product footage is
    present. Fall back to the other camera-native "talking_head" candidates
    (minus the one chosen as the real take) rather than touching
    `inventory()` itself (out of scope for this task, see task 8).
    """
    if inv.get("broll"):
        return inv["broll"]
    return [p for p in _camera_native(inv.get("talking_head") or []) if p != th]


def _resolve(folder: Path, p: Path | str) -> Path:
    """Resolve an explicit source path against `folder` (unless already absolute)."""
    p = Path(p)
    return p if p.is_absolute() else folder / p


def _load_output_root(config_path: Path | str) -> Path:
    data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    return Path(data.get("output_dir", "./output"))


def _ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def _transcribe_words(audio_path: Path, out_json: Path) -> list[tuple[float, float, str]]:
    from src.transcribe import transcribe
    t = transcribe(audio_path, out_json)
    return [(w.start, w.end, w.word) for w in t.words]


def _concat(clips: list[Path], out_path: Path) -> Path:
    list_file = out_path.with_suffix(".concat.txt")
    list_file.write_text("".join(f"file '{Path(c).resolve()}'\n" for c in clips), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c", "copy", str(out_path)],
        check=True,
    )
    return out_path


def _render_hook(style_id, style, th, hook_s, hook_dur, hook_front_ass, graphic, car_png, shadow_png, out_dir):
    hook_clip = out_dir / f"hook_{style_id}.mp4"
    if graphic is not None:
        fly_s, fly_e = round(hook_dur * 0.35, 2), round(hook_dur * 0.9, 2)
        compose_hook(th, hook_s, hook_dur, hook_front_ass, None, None, car_png, shadow_png,
                     (fly_s, fly_e), style, FONTS_DIR, hook_clip)
    else:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{hook_s:.3f}", "-t", f"{hook_dur:.3f}", "-i", str(th),
             "-vf", f"scale=1080:1920:flags=lanczos,setsar=1,fps=30,format=yuv420p,"
                    f"ass={hook_front_ass}:fontsdir={FONTS_DIR}",
             "-c:v", "libx264", "-crf", "18", "-preset", "medium",
             "-color_range", "tv", "-colorspace", "bt709", "-color_trc", "bt709", "-color_primaries", "bt709",
             "-an", str(hook_clip)],
            check=True,
        )
    return hook_clip


def build_shnurok(
    folder: Path,
    styles=("classic", "bold"),
    config_path: str = "config.yaml",
    talking_head: Path | str | None = None,
    voice: Path | str | None = None,
    broll: list[Path | str] | None = None,
    graphic: Path | str | None = None,
    music: Path | str | None = None,
) -> dict[str, Path]:
    """Build a full shnurok reel per style, written under the versioned
    output layout.

    Source selection: `talking_head`/`voice`/`broll`/`graphic`/`music` are
    resolved relative to `folder` (unless already absolute); `broll` is a
    list of paths. Pass these explicitly whenever the caller already knows
    the folder's structure — the operating agent does, since it just
    classified `raw/` into this folder per CLAUDE.md's inbox workflow, and
    it decides which take/clips belong in the reel. When a source is left
    `None`, `inventory()` is used as a best-effort auto-pick fallback (for
    quick/unattended use); see `_pick_talking_head`/`_broll_fallback` for
    its heuristics and the warning it logs when a talking-head pick is
    ambiguous — that warning means "verify this, or pass talking_head=...".
    """
    folder = Path(folder)
    need_inventory = any(v is None for v in (talking_head, voice, broll, graphic, music))
    inv = inventory(folder) if need_inventory else {}

    th = _resolve(folder, talking_head) if talking_head is not None else _pick_talking_head(inv)

    if voice is not None:
        voice = _resolve(folder, voice)
    elif inv.get("voice"):
        voice = inv["voice"][0]
    else:
        raise ValueError("build_shnurok: no voiceover (voice) file found in inventory (pass voice=... explicitly)")

    music = _resolve(folder, music) if music is not None else (inv["music"][0] if inv.get("music") else DEFAULT_MUSIC)

    if broll is not None:
        broll = order_broll([_resolve(folder, p) for p in broll])
    else:
        broll = order_broll(_broll_fallback(inv, th))
    if not broll:
        raise ValueError("build_shnurok: no b-roll clips found in inventory (pass broll=[...] explicitly)")

    graphic = _resolve(folder, graphic) if graphic is not None else (inv["graphic"][0] if inv.get("graphic") else None)

    slug = folder.name
    output_root = _load_output_root(config_path)
    base_dir = output_root / slug
    out_dir = versioned_dir(base_dir)

    th_words = _transcribe_words(th, out_dir / "th.transcript.json")
    (hook_s, hook_e), (cta_s, cta_e) = split_hook_cta(th_words)
    hook_dur, cta_dur = hook_e - hook_s, cta_e - cta_s

    body_dur = _ffprobe_duration(voice)
    body_words = _transcribe_words(voice, out_dir / "voice.transcript.json")

    total_dur = hook_dur + body_dur + cta_dur

    car_png = shadow_png = None
    if graphic is not None:
        car_png, shadow_png = out_dir / "car.png", out_dir / "car_shadow.png"
        cutout_graphic(graphic, car_png, shadow_png)

    results: dict[str, Path] = {}
    for style_id in styles:
        style = load_style(style_id, config_path)

        # a. hook titles
        hook_words_rel = [(s - hook_s, e - hook_s, w) for (s, e, w) in th_words if hook_s <= s < hook_e]
        front = hook_lines_from_words(hook_words_rel)
        hook_front_ass = out_dir / f"hook_front_{style_id}.ass"
        hook_titles_ass(front, None, style, hook_front_ass)

        # b. graphic + hook clip
        hook_clip = _render_hook(style_id, style, th, hook_s, hook_dur, hook_front_ass,
                                  graphic, car_png, shadow_png, out_dir)

        # c. body
        body_clip = render_body(style_id, style, broll, body_dur, out_dir, _concat)

        # d. cta clip
        cta_clip = render_cta(style_id, th, cta_s, cta_dur, out_dir)

        # e. concat -> silent video
        silent_video = out_dir / f"silent_{style_id}.mp4"
        _concat([hook_clip, body_clip, cta_clip], silent_video)

        # f. body subs
        shifted_body = [(s + hook_dur, e + hook_dur, w) for (s, e, w) in body_words]
        body_ass = out_dir / f"body_subs_{style_id}.ass"
        word_subs_ass(shifted_body, style, body_ass, window=(hook_dur, hook_dur + body_dur))

        # g. cta titles
        cta_off = hook_dur + body_dur
        cta_words_final = [
            (s - cta_s + cta_off, e - cta_s + cta_off, w) for (s, e, w) in th_words if cta_s <= s < cta_e
        ]
        screens = cta_screens_from_words(cta_words_final)
        cta_ass = out_dir / f"cta_titles_{style_id}.ass"
        cta_titles_ass(screens, style, cta_ass)

        # h. audio
        audio_out = out_dir / f"audio_{style_id}.m4a"
        mix_voice_music(
            [(th, hook_s, hook_dur), (voice, 0.0, body_dur), (th, cta_s, cta_dur)],
            music, audio_out, style, total_dur,
        )

        # i. final mux + burn
        final_path = out_dir / f"final_{style_id}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(silent_video), "-i", str(audio_out),
             "-vf", f"ass={body_ass}:fontsdir={FONTS_DIR},ass={cta_ass}:fontsdir={FONTS_DIR}",
             "-map", "0:v", "-map", "1:a",
             "-c:v", "libx264", "-crf", "18", "-preset", "medium",
             "-color_range", "tv", "-colorspace", "bt709", "-color_trc", "bt709", "-color_primaries", "bt709",
             "-c:a", "copy", "-t", f"{total_dur:.3f}", "-shortest", "-movflags", "+faststart", str(final_path)],
            check=True,
        )
        results[style_id] = final_path

    update_latest(base_dir, out_dir.name)
    return results
