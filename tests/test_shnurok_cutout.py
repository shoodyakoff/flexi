from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from src.shnurok.cutout import cutout_graphic

def _make_src(p: Path):
    img = Image.new("RGB", (300, 200), (255, 255, 255))      # white bg
    d = ImageDraw.Draw(img)
    d.ellipse([90, 60, 209, 139], fill=(120, 120, 120))      # grey object; bbox corners stay white
    d.rectangle([145, 95, 154, 104], fill=(255, 255, 255))   # white detail INSIDE the object
    img.save(p)

def test_cutout_removes_bg_keeps_inner_white(tmp_path):
    src = tmp_path / "in.png"; out = tmp_path / "out.png"; _make_src(src)
    w, h = cutout_graphic(src, out)
    im = np.asarray(Image.open(out).convert("RGBA"))
    # tight crop ~ object bounds (120x80), small tolerance for erode
    assert 110 <= w <= 122 and 72 <= h <= 82
    alpha = im[:, :, 3]
    assert alpha[0, 0] == 0                         # corner transparent
    # inner white detail stays opaque (not keyed out)
    assert alpha[h // 2, w // 2] > 200

def test_cutout_writes_shadow(tmp_path):
    src = tmp_path / "in.png"; out = tmp_path / "o.png"; sh = tmp_path / "sh.png"; _make_src(src)
    cutout_graphic(src, out, shadow_png=sh)
    assert sh.exists()
