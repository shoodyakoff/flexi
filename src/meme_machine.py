# src/meme_machine.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.meme_video import Box, W, H, FPS  # переиспользуем константы/типы


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
