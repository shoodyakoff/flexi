from __future__ import annotations
import glob, os, subprocess, tempfile
from pathlib import Path
from PIL import Image, ImageFilter

def person_matte(clip, start, dur, out_mp4, fps: int = 30):
    from rembg import remove, new_session
    out_mp4 = Path(out_mp4)
    with tempfile.TemporaryDirectory() as td:
        fr, mk = Path(td) / "fr", Path(td) / "mk"
        fr.mkdir(); mk.mkdir()
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start}", "-t", f"{dur}", "-i", str(clip),
                        "-vf", f"scale=1080:1920:flags=lanczos,setsar=1,fps={fps}",
                        str(fr / "f_%04d.png")], check=True)
        sess = new_session("u2net_human_seg")
        for f in sorted(glob.glob(str(fr / "f_*.png"))):
            m = remove(Image.open(f), session=sess, only_mask=True)
            m.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(1.2)) \
             .save(mk / os.path.basename(f))
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(fps),
                        "-i", str(mk / "f_%04d.png"), "-vf", "format=gray",
                        "-c:v", "libx264", "-crf", "12", "-preset", "fast", str(out_mp4)], check=True)
    return out_mp4
