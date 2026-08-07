from __future__ import annotations
import subprocess
from pathlib import Path
from PIL import Image


def compose_hook(th_clip, th_start, dur, front_ass, behind_ass, matte_mp4, car_png,
                 shadow_png, keyword_window, style, fonts_dir, out_mp4):
    fly_s, fly_e = keyword_window
    tx = style.graphic_center_x - style.graphic_width // 2
    carx = f"max({tx}, 1080-(t-{fly_s})/{style.fly_dur}*(1080-{tx}))"
    # car height from aspect after scaling to graphic_width (probe png)
    cw, ch = Image.open(car_png).size
    car_h = round(style.graphic_width * ch / cw)
    car_y = style.graphic_center_y - car_h // 2

    inputs = ["-ss", f"{th_start}", "-t", f"{dur}", "-i", str(th_clip)]

    if behind_ass is not None:
        # Text-behind-subject: keyword burned on base, person matte redrawn
        # over it (via alphamerge), then the rest of the staircase titles on
        # top, then the graphic fly-in.
        inputs += ["-i", str(matte_mp4), "-i", str(shadow_png), "-i", str(car_png)]
        fc = (
            "[0:v]scale=1080:1920:flags=lanczos,setsar=1,fps=30,format=yuv420p,split=2[b1][b2];"
            f"[b1]ass={behind_ass}:fontsdir={fonts_dir}[bh];"
            "[1:v]scale=1080:1920,format=gray,fps=30[mk];"
            "[b2][mk]alphamerge[person];"
            f"[bh][person]overlay=0:0:enable='between(t,{fly_s},{fly_e})'[pb];"
            f"[pb]ass={front_ass}:fontsdir={fonts_dir}[ft];"
            f"[2:v]scale={style.graphic_width}:-1[sh];[3:v]scale={style.graphic_width}:-1[car];"
            f"[ft][sh]overlay=x='{carx}'+14:y={car_y + 18}:enable='between(t,{fly_s},{fly_e})'[s1];"
            f"[s1][car]overlay=x='{carx}':y={car_y}:enable='between(t,{fly_s},{fly_e})'[v]"
        )
    else:
        # Plain hook: no text-behind-subject, no matte — skip the
        # alphamerge/person-overlay layers entirely.
        inputs += ["-i", str(shadow_png), "-i", str(car_png)]
        fc = (
            "[0:v]scale=1080:1920:flags=lanczos,setsar=1,fps=30,format=yuv420p[base];"
            f"[base]ass={front_ass}:fontsdir={fonts_dir}[ft];"
            f"[1:v]scale={style.graphic_width}:-1[sh];[2:v]scale={style.graphic_width}:-1[car];"
            f"[ft][sh]overlay=x='{carx}'+14:y={car_y + 18}:enable='between(t,{fly_s},{fly_e})'[s1];"
            f"[s1][car]overlay=x='{carx}':y={car_y}:enable='between(t,{fly_s},{fly_e})'[v]"
        )

    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    *inputs,
                    "-filter_complex", fc, "-map", "[v]", "-an",
                    "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                    "-color_range", "tv", "-colorspace", "bt709", "-color_trc", "bt709",
                    "-color_primaries", "bt709", str(out_mp4)], check=True)
    return Path(out_mp4)
