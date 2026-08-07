from __future__ import annotations
from collections import deque
from pathlib import Path
import numpy as np
from PIL import Image, ImageFilter

def cutout_graphic(src, out_png, shadow_png=None, white_thresh: int = 238):
    img = Image.open(src).convert("RGB")
    arr = np.asarray(img)
    h, w = arr.shape[:2]
    white = (arr[:, :, 0] > white_thresh) & (arr[:, :, 1] > white_thresh) & (arr[:, :, 2] > white_thresh)
    bg = np.zeros((h, w), bool)
    dq = deque()
    for sx, sy in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if white[sy, sx]:
            bg[sy, sx] = True; dq.append((sy, sx))
    while dq:
        y, x = dq.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and not bg[ny, nx] and white[ny, nx]:
                bg[ny, nx] = True; dq.append((ny, nx))
    alpha = np.where(bg, 0, 255).astype(np.uint8)
    im = Image.fromarray(np.dstack([arr, alpha]), "RGBA")
    im.putalpha(im.getchannel("A").filter(ImageFilter.MinFilter(3)))  # erode 1px
    im = im.crop(im.getbbox())
    im.save(out_png)
    if shadow_png is not None:
        a = im.getchannel("A")
        sh = Image.composite(Image.new("RGBA", im.size, (0, 0, 0, 255)),
                             Image.new("RGBA", im.size, (0, 0, 0, 0)), a)
        sh.putalpha(a.point(lambda v: int(v * 0.55)))
        sh.filter(ImageFilter.GaussianBlur(14)).save(shadow_png)
    return im.size
