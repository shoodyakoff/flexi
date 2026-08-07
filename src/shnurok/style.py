from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass(frozen=True)
class StyleConfig:
    style_id: str
    title_font: str
    word_font: str
    hook_step: int
    hook_size: int
    cta_size: int
    word_size: int
    accent_bgr: str
    use_accent: bool
    uppercase_words: bool
    tilt: bool
    transitions: str
    graphic_width: int
    graphic_center_x: int
    graphic_center_y: int
    fly_dur: float
    behind_fontsize: int
    sub_lufs: float
    music_lufs: float
    limiter: float


def load_style(style_id: str, config_path: Path | str = "config.yaml") -> StyleConfig:
    data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))["shnurok"]
    st = data["styles"][style_id]  # KeyError if unknown
    g, ld = data["graphic"], data["loudness"]
    return StyleConfig(
        style_id=style_id,
        title_font=st["title_font"], word_font=st["word_font"],
        hook_step=data["hook_step"], hook_size=st["hook_size"],
        cta_size=st["cta_size"], word_size=st["word_size"],
        accent_bgr=data["accent_bgr"], use_accent=st["use_accent"],
        uppercase_words=st["uppercase_words"], tilt=st["tilt"],
        transitions=st["transitions"],
        graphic_width=g["width"], graphic_center_x=g["center_x"], graphic_center_y=g["center_y"],
        fly_dur=data["fly_dur"], behind_fontsize=data["behind_fontsize"],
        sub_lufs=ld["sub_lufs"], music_lufs=ld["music_lufs"], limiter=ld["limiter"],
    )
