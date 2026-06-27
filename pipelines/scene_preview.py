"""Scene preview stand — render each of the 4 palette scenes as a short clip.

We iterate on the *look* of every scene type in isolation, without rebuilding the
whole assembled video each time. Each scene is rendered from sample media:

  Scene 1  format_1_hook_metal      head + dynamic Bebas word-stickers (red echo)
  Scene 2  format_4_blue_demo       blue grid + product demo, fs picto captions
  Scene 3  format_5_lower_demo_cta  head + demo block sliding up, Bebas captions
  Scene 4  format_2_framed_face     close-up head, Hummus captions on a dark plate

Usage:
    .venv/bin/python pipelines/scene_preview.py            # all four
    .venv/bin/python pipelines/scene_preview.py 2 4        # only scenes 2 and 4

Outputs to output/scene_previews/scene_<n>.mp4 (+ a mid-frame scene_<n>.png).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import render_ref_style_directed as r  # noqa: E402  (pipelines/ on path via ROOT)
from src.ref_style_director import RefStyleEditPlan, RefStyleEditSegment  # noqa: E402
from src.schemas import Transcript, Word  # noqa: E402

HEAD = ROOT / "output/matrix/cleaned/IMG_5106.mp4"
DEMO = ROOT / "assets/broll_brand/demo_match_1.mp4"
MUSIC = ROOT / "assets/music/provocative.mp3"
BG = ROOT / "assets/backgrounds/bg_main.mp4"  # animated blue-grid backdrop (Scene 2)
FONTS = ROOT / "assets/fonts"
OUT = ROOT / "output/scene_previews"

# Each scene: which format to drive, the caption words, and the spoken role.
SCENES = {
    1: dict(
        format_id="format_1_hook_metal",
        role="hook_problem",
        words=["хочешь", "сильное", "резюме", "смотри"],
    ),
    2: dict(
        format_id="format_4_blue_demo",
        role="product_demo",
        words=["анализ", "резюме", "через", "атс"],
    ),
    3: dict(
        format_id="format_5_lower_demo_cta",
        role="cta",
        words=["ссылка", "в", "шапке", "профиля"],
    ),
    4: dict(
        format_id="format_2_framed_face",
        role="workflow_explain",
        words=["сервис", "адаптирует", "резюме", "сам"],
    ),
}

DURATION = 3.4


def silent_sfx() -> Path:
    """render_base always wires an SFX input even when no SFX events fire."""
    path = OUT / "_silent.m4a"
    if not path.exists():
        r.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-t", "1.0", "-c:a", "aac", str(path),
            ],
            "make silent sfx",
        )
    return path


def build_transcript(words: list[str], duration: float) -> Transcript:
    n = len(words)
    span = duration / n
    timed = [
        Word(word=w, start=round(i * span + 0.10, 3), end=round((i + 1) * span - 0.06, 3))
        for i, w in enumerate(words)
    ]
    return Transcript(words=timed, full_text=" ".join(words), duration=duration)


def build_plan(format_id: str, role: str, duration: float) -> RefStyleEditPlan:
    seg = RefStyleEditSegment(
        start=0.0,
        end=duration,
        text="preview",
        semantic_role=role,
        format_id=format_id,
        subtitle_mode="preview",
        product_demo=format_id in {"format_4_blue_demo", "format_5_lower_demo_cta"},
        reason="scene preview",
        confidence=1.0,
    )
    return RefStyleEditPlan(source=HEAD.name, duration=duration, segments=[seg])


def render_scene(n: int, spec: dict, sfx: Path) -> Path:
    base = OUT / f"scene_{n}.base.mp4"
    final = OUT / f"scene_{n}.mp4"
    transcript = build_transcript(spec["words"], DURATION)
    plan = build_plan(spec["format_id"], spec["role"], DURATION)

    r.render_base(
        base,
        source=HEAD,
        product=[DEMO],
        music=MUSIC,
        sfx_swish=sfx,
        duration=DURATION,
        plan=plan,
        product_tags=[[]],
        head_cutaways=False,
        bg_video=BG if BG.exists() else None,
    )
    ass = OUT / f"scene_{n}.ass"
    r.write_ass(ass, transcript, plan)
    r.burn_ass(base, ass, final, FONTS)

    # Grab a mid-frame for a quick eyeball check.
    png = OUT / f"scene_{n}.png"
    r.run(
        ["ffmpeg", "-y", "-ss", "1.6", "-i", str(final), "-frames:v", "1", str(png)],
        f"grab scene {n} frame",
    )
    return final


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for asset in (HEAD, DEMO, MUSIC):
        if not asset.exists():
            raise SystemExit(f"missing sample asset: {asset}")
    which = [int(a) for a in sys.argv[1:]] or sorted(SCENES)
    sfx = silent_sfx()
    for n in which:
        if n not in SCENES:
            print(f"!! no scene {n}", flush=True)
            continue
        out = render_scene(n, SCENES[n], sfx)
        print(f"== scene {n} -> {out}", flush=True)


if __name__ == "__main__":
    main()
