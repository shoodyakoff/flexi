"""Body-beat rendering for the shnurok build orchestrator (`build.py`).

Cuts each b-roll clip to its slice of the body window, applies the
per-style effect (classic: slow-zoom when the per-clip slice is long
enough to read as motion rather than a flash; bold: white flash-in on
every clip + a crop-punch on every 4th), and hard-cut concatenates the
result into one clip spanning `body_dur`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def enc_cut(out, src, off, dur, flash=False, punch=False, slowzoom=False):
    vf = ["scale=1080:1920:flags=lanczos", "setsar=1", "fps=30"]
    if punch:
        vf += ["crop=iw/1.13:ih/1.13:x=(iw-ow)/2:y=(ih-oh)*0.40", "scale=1080:1920:flags=lanczos"]
    if slowzoom:
        vf = ["scale=2160:3840:flags=lanczos", "setsar=1", "fps=30",
              "zoompan=z='min(1+0.0008*on,1.07)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1080x1920:fps=30"]
    vf.append("format=yuv420p")
    if flash:
        vf.append("fade=t=in:st=0:d=0.07:color=white")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{off:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
                    "-vf", ",".join(vf), "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                    "-color_range", "tv", "-colorspace", "bt709", "-color_trc", "bt709", "-color_primaries", "bt709",
                    "-an", str(out)], check=True)
    return Path(out)


def render_body(style_id, style, broll, body_dur, out_dir, concat_fn) -> Path:
    """Cut+concat the body beat. `concat_fn(clips, out_path)` is build.py's
    concat-demuxer helper (shared with the hook/body/cta final concat)."""
    per = body_dur / len(broll)
    cuts = []
    for i, clip in enumerate(broll):
        dur_i = per if i < len(broll) - 1 else body_dur - per * (len(broll) - 1)
        flash = style_id == "bold"
        punch = style_id == "bold" and (i + 1) % 4 == 0
        slowzoom = style_id == "classic" and per >= 1.9
        cut_path = out_dir / f"body_{style_id}_{i:02d}.mp4"
        cuts.append(enc_cut(cut_path, clip, 0.0, dur_i, flash=flash, punch=punch, slowzoom=slowzoom))
    body_clip = out_dir / f"body_{style_id}.mp4"
    return concat_fn(cuts, body_clip)
