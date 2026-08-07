"""CTA-beat rendering for the shnurok build orchestrator (`build.py`).

Plain CFR cut of the talking-head CTA span, with the bold style's
white flash-in applied (matching the body-beat treatment for that style).
"""
from __future__ import annotations

from pathlib import Path

from src.shnurok.render_body import enc_cut


def render_cta(style_id, th, cta_s, cta_dur, out_dir) -> Path:
    cta_clip = out_dir / f"cta_{style_id}.mp4"
    return enc_cut(cta_clip, th, cta_s, cta_dur, flash=(style_id == "bold"))
