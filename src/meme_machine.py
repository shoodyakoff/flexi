# src/meme_machine.py
from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.meme_video import Box, W, H, FPS, VIDEO_EXTENSIONS, _sort_key  # переиспользуем константы/типы
from src.meme_video import ROOT as _ROOT  # корень проекта для резолва путей шрифтов
from src.meme_video import _video_normalize_filter
from src.meme_video import _filter_path, text_for_overlay
from src.meme_video import (
    build_concat_command, build_dedup_command, build_qa_sheet_command,
    write_concat_list, run_command, punch_filename_stem,
)
from src.output_paths import versioned_dir


@dataclass(frozen=True)
class Beat:
    role: str
    fill: str            # "face" | "source"
    caption_pos: str     # "top" | "bottom"
    caption_mode: str    # "own" | "keep" | "overlay"
    cover: Box | None = None


@dataclass(frozen=True)
class MemePlan:
    source: Path
    drop_at: float
    beats: list[Beat]


@dataclass(frozen=True)
class CaptionPair:
    a: str
    b: str | None = None


def scene_a_length(clip_len: float, drop_at: float) -> float:
    return min(clip_len, drop_at)


def audio_start(clip_len: float, drop_at: float) -> float:
    return round(drop_at - scene_a_length(clip_len, drop_at), 6)


def load_plan(path: Path) -> MemePlan:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    beats = [
        Beat(
            role=b["role"],
            fill=b["fill"],
            caption_pos=b.get("caption_pos", "top"),
            caption_mode=b.get("caption_mode", "own"),
            cover=Box(**b["cover"]) if b.get("cover") else None,
        )
        for b in data["beats"]
    ]
    return MemePlan(source=Path(data["source"]), drop_at=float(data["drop_at"]), beats=beats)


def load_pairs(path: Path) -> list[CaptionPair]:
    rows = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    return [CaptionPair(a=str(r["a"]).strip(), b=(str(r["b"]).strip() if r.get("b") else None)) for r in rows]


def collect_face_clips(directory: Path) -> list[Path]:
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    return sorted(
        (p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS),
        key=_sort_key,
    )


def _stem_number(path: Path) -> int | None:
    m = re.match(r"^(\d+)", path.stem.strip())
    return int(m.group(1)) if m else None


def parse_faces_subset(spec: str, available: list[Path]) -> list[Path]:
    wanted: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lo, hi = token.split("-", 1)
            wanted.update(range(int(lo), int(hi) + 1))
        else:
            wanted.add(int(token))
    return [p for p in available if (_stem_number(p) in wanted)]


def assign_faces(n: int, faces: list[Path], seed: int) -> list[Path]:
    if not faces:
        raise ValueError("No face clips available for assignment")
    rng = random.Random(seed)
    shuffled = faces[:]
    rng.shuffle(shuffled)
    return [shuffled[i % len(shuffled)] for i in range(n)]


_ENC = [
    "-c:v", "libx264", "-preset", "medium", "-crf", "18",
    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
]


def build_face_scene_command(
    *,
    face_path: Path,
    source_path: Path,
    output_path: Path,
    scene_a_len: float,
    audio_start: float,
) -> list[str]:
    dur = f"{scene_a_len:.3f}"
    astart = f"{audio_start:.3f}"
    filter_complex = (
        f"[1:v]{_video_normalize_filter()}[v];"
        f"[0:a]atrim=start=0:duration={dur},asetpts=PTS-STARTPTS,"
        "aformat=sample_rates=48000:channel_layouts=stereo[a]"
    )
    return [
        "ffmpeg", "-nostdin", "-y",
        "-ss", astart, "-t", dur, "-i", str(source_path),   # звук исходника (окно [T-a, T])
        "-t", dur, "-i", str(face_path),                    # видео лица (обрезано до a)
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        *_ENC, str(output_path),
    ]


def build_punch_scene_command(
    *,
    source_path: Path,
    output_path: Path,
    drop_at: float,
) -> list[str]:
    filter_complex = (
        f"[0:v]{_video_normalize_filter()}[v];"
        "[0:a]asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo[a]"
    )
    return [
        "ffmpeg", "-nostdin", "-y",
        "-ss", f"{drop_at:.3f}", "-i", str(source_path),
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        *_ENC, str(output_path),
    ]


@dataclass(frozen=True)
class VariantLook:
    color: str
    font_file: Path


@dataclass(frozen=True)
class MemeCaptionCfg:
    font_regular: Path
    font_bold: Path
    palette: list[str]
    bold_cycle: list[bool]
    top_box: Box
    bottom_box: Box
    font_size: int = 72
    max_chars_per_line: int = 20


def variant_look(index: int, cfg: MemeCaptionCfg) -> VariantLook:
    color = cfg.palette[index % len(cfg.palette)]
    bold = cfg.bold_cycle[index % len(cfg.bold_cycle)]
    return VariantLook(color=color, font_file=(cfg.font_bold if bold else cfg.font_regular))


def _box_for(pos: str, cfg: MemeCaptionCfg) -> Box:
    return cfg.top_box if pos == "top" else cfg.bottom_box


def _drawtext(
    *, text: str, look: VariantLook, box: Box, cfg: MemeCaptionCfg, enable: str, text_file: Path
) -> str:
    lines = text_for_overlay(text, max_chars_per_line=cfg.max_chars_per_line)
    # Читаем многострочный текст из файла (textfile=), а не инлайном (text=): сырой
    # перевод строки внутри text='...' этот билд ffmpeg рисует как .notdef-глиф —
    # квадрат «нет глифа» на каждом переносе. Проверенный паттерн из meme_video.
    Path(text_file).write_text(lines, encoding="utf-8")
    return (
        "drawtext="
        f"fontfile={_filter_path(look.font_file)}:"
        f"textfile={_filter_path(text_file)}:"
        f"fontcolor={look.color}:"
        f"fontsize={cfg.font_size}:line_spacing=8:"
        "borderw=6:bordercolor=black@0.85:shadowx=2:shadowy=2:shadowcolor=black@0.5:"
        "text_align=C:"
        f"x=(w-text_w)/2:y={box.y}+({box.h}-text_h)/2:"
        f"enable='{enable}'"
    )


def build_caption_overlay_command(*, input_path, output_path, total_dur, scene_a_len,
                                  plan, pair, look, cfg, work_dir: Path):
    a_end = f"{scene_a_len:.3f}"
    total = f"{total_dur:.3f}"
    setup_beat, punch_beat = plan.beats[0], plan.beats[1]
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    filters: list[str] = []

    # подпись A на сцене A
    filters.append(_drawtext(
        text=pair.a, look=look, box=_box_for(setup_beat.caption_pos, cfg), cfg=cfg,
        enable=f"between(t,0.000,{a_end})", text_file=work_dir / "cap_a.txt",
    ))

    # подпись B на сцене B (только режим overlay + текст задан)
    if punch_beat.caption_mode == "overlay" and pair.b:
        enable_b = f"between(t,{a_end},{total})"
        if punch_beat.cover is not None:
            filters.append(punch_beat.cover.drawbox_filter("black", enable=enable_b))
        filters.append(_drawtext(
            text=pair.b, look=look, box=_box_for(punch_beat.caption_pos, cfg), cfg=cfg,
            enable=enable_b, text_file=work_dir / "cap_b.txt",
        ))

    filters += ["setpts=PTS-STARTPTS", "format=yuv420p"]
    vf = ",".join(filters)
    return [
        "ffmpeg", "-nostdin", "-y", "-i", str(input_path),
        "-vf", vf, "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-bf", "0",
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
        str(output_path),
    ]


@dataclass(frozen=True)
class MemeVariant:
    index: int
    pair: CaptionPair
    face_path: Path
    final_path: Path


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def plan_variants(*, pairs, faces, seed, publish_dir):
    assigned = assign_faces(len(pairs), faces, seed)
    variants: list[MemeVariant] = []
    for i, (pair, face) in enumerate(zip(pairs, assigned), start=1):
        stem = punch_filename_stem(pair.a, max_words=5)
        variants.append(MemeVariant(
            index=i, pair=pair, face_path=face,
            final_path=publish_dir / f"{i:02d}_{stem}.mp4",
        ))
    return variants


def build_variant(*, variant, plan, look, cfg, work_dir):
    job = work_dir / f"{variant.index:02d}_{punch_filename_stem(variant.pair.a, max_words=5)}"
    job.mkdir(parents=True, exist_ok=True)
    scene_a, scene_b = job / "sceneA.mp4", job / "sceneB.mp4"
    concat_list, assembled = job / "concat.txt", job / "assembled.mp4"
    dedup, final, qa = job / "dedup.mp4", job / "final.mp4", job / "qa_sheet.jpg"

    face_len = probe_duration(variant.face_path)
    a_len = scene_a_length(face_len, plan.drop_at)
    a_start = audio_start(face_len, plan.drop_at)

    run_command(build_face_scene_command(
        face_path=variant.face_path, source_path=plan.source, output_path=scene_a,
        scene_a_len=a_len, audio_start=a_start,
    ))
    run_command(build_punch_scene_command(
        source_path=plan.source, output_path=scene_b, drop_at=plan.drop_at,
    ))
    write_concat_list([scene_a, scene_b], concat_list)
    run_command(build_concat_command(concat_list, assembled))
    run_command(build_dedup_command(assembled, dedup))

    total = probe_duration(dedup)
    run_command(build_caption_overlay_command(
        input_path=dedup, output_path=final, total_dur=total, scene_a_len=a_len,
        plan=plan, pair=variant.pair, look=look, cfg=cfg, work_dir=job,
    ))
    run_command(build_qa_sheet_command(final, qa))
    variant.final_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(final, variant.final_path)
    return final


def run_batch(*, plan, pairs, faces, series, seed, cfg, work_root, publish_root):
    publish_dir = Path(publish_root) / series
    work_dir = versioned_dir(Path(work_root) / series)
    variants = plan_variants(pairs=pairs, faces=faces, seed=seed, publish_dir=publish_dir)
    finals: list[Path] = []
    for v in variants:
        look = variant_look(v.index - 1, cfg)
        finals.append(build_variant(variant=v, plan=plan, look=look, cfg=cfg, work_dir=work_dir))
    return finals


def caption_cfg_from_config(cfg) -> MemeCaptionCfg:
    """Map the pydantic MemeConfig (cfg.meme) into a MemeCaptionCfg.

    Relative font paths resolve against the project root; absolute paths pass through.
    """
    m = cfg.meme

    def _p(rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else _ROOT / p

    return MemeCaptionCfg(
        font_regular=_p(m.font_regular), font_bold=_p(m.font_bold),
        palette=list(m.palette), bold_cycle=list(m.bold_cycle),
        top_box=Box(*m.top_box), bottom_box=Box(*m.bottom_box),
        font_size=m.font_size, max_chars_per_line=m.max_chars_per_line,
    )
