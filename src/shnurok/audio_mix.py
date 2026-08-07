from __future__ import annotations
import re, subprocess
from pathlib import Path

_I_RE = re.compile(r"\bI:\s*(-?\d+(?:\.\d+)?)\s*LUFS")

def parse_ebur128_i(stderr: str) -> float:
    matches = _I_RE.findall(stderr)
    if not matches:
        raise ValueError("no integrated loudness in ebur128 output")
    return float(matches[-1])

def measure_lufs(path, start=None, dur=None) -> float:
    af = "ebur128"
    if start is not None or dur is not None:
        trim = "atrim=" + (f"{start}" if start is not None else "0")
        if dur is not None:
            trim += f":{(start or 0) + dur}"
        af = f"{trim},asetpts=PTS-STARTPTS,ebur128"
    r = subprocess.run(["ffmpeg", "-i", str(path), "-af", af, "-f", "null", "-"],
                       capture_output=True, text=True)
    return parse_ebur128_i(r.stderr)

def gain_db(measured_lufs: float, target_lufs: float) -> float:
    return target_lufs - measured_lufs

def mix_voice_music(segments, music, out_path, style, total_dur) -> None:
    inputs, filt, labels = [], [], []
    for i, (path, start, dur) in enumerate(segments):
        inputs += ["-i", str(path)]
        g = gain_db(measure_lufs(path, start, dur), style.sub_lufs)
        filt.append(
            f"[{i}:a]atrim={start}:{start + dur},asetpts=PTS-STARTPTS,"
            f"volume={g:.2f}dB,aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[s{i}]"
        )
        labels.append(f"[s{i}]")
    mi = len(segments)
    inputs += ["-i", str(music)]
    mg = gain_db(measure_lufs(music), style.music_lufs)
    filt.append(
        f"[{mi}:a]aloop=loop=-1:size=2e9,atrim=0:{total_dur},asetpts=PTS-STARTPTS,"
        f"volume={mg:.2f}dB,afade=t=out:st={total_dur - 1.2}:d=1.2,"
        f"aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[m]"
    )
    filt.append("".join(labels) + f"concat=n={len(segments)}:v=0:a=1[sp]")
    filt.append(f"[sp][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                f"alimiter=limit={style.limiter}:level=disabled[a]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(filt),
                    "-map", "[a]", "-c:a", "aac", "-b:a", "192k", str(out_path)], check=True)
