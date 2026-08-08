#!/usr/bin/env python3
"""Patch Gilroy TTFs (family name -> "Gilroy Heavy"/"Gilroy Bold", subfamily Regular)
and copy them into assets/fonts/ so libass finds them by family for the shnurok titles.

Gilroy is a COMMERCIAL font — the copied files are gitignored (see assets/fonts/.gitignore).
The operator must have the licensed Gilroy TTFs in ~/Library/Fonts/ (or edit SRC below).

    python pipelines/shnurok/_prepare_fonts.py
"""
from __future__ import annotations
import os
from pathlib import Path
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[2]
DST = ROOT / "assets" / "fonts"
JOBS = [
    (Path.home() / "Library/Fonts/Gilroy-Heavy_0.ttf", "Gilroy Heavy"),
    (Path.home() / "Library/Fonts/Gilroy-Bold_0.ttf", "Gilroy Bold"),
]


def _set_name(rec, value: str):
    is_utf16 = b"\x00" in rec.toBytes()
    rec.string = value.encode("utf-16-be") if is_utf16 else value.encode("latin-1")


def patch(src: Path, family: str, out: Path):
    f = TTFont(src)
    name = f["name"]
    ps = family.replace(" ", "")
    for rec in name.names:
        if rec.nameID in (1, 16):       # family / typographic family
            _set_name(rec, family)
        elif rec.nameID == 2:           # subfamily -> Regular (no separate bold face)
            _set_name(rec, "Regular")
        elif rec.nameID == 4:           # full name
            _set_name(rec, family)
        elif rec.nameID == 6:           # postscript name
            _set_name(rec, ps)
    f.save(out)


def main():
    DST.mkdir(parents=True, exist_ok=True)
    for src, family in JOBS:
        if not src.exists():
            print(f"⚠ missing source font: {src} — drop the licensed Gilroy TTF there")
            continue
        out = DST / (family.replace(" ", "") + ".ttf")
        patch(src, family, out)
        chk = TTFont(out)
        print(f"✓ {out.name}: family={chk['name'].getDebugName(1)!r}")


if __name__ == "__main__":
    main()
