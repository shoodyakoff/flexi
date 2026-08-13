#!/usr/bin/env python3
# pipelines/qa_meme.py
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def check_format(width: int, height: int, fps: float, has_audio: bool) -> list[str]:
    fails: list[str] = []
    if (width, height) != (1080, 1920):
        fails.append(f"формат {width}x{height} != 1080x1920")
    if not (29.0 <= fps <= 31.0):
        fails.append(f"fps {fps} вне 29–31")
    if not has_audio:
        fails.append("нет audio-дорожки")
    return fails


def check_seam(total_dur: float, scene_a_len: float, drop_at: float, tol: float = 0.15) -> list[str]:
    fails: list[str] = []
    if scene_a_len > drop_at + tol:
        fails.append(f"сцена A ({scene_a_len:.2f}s) длиннее разгона до дропа ({drop_at:.2f}s)")
    return fails


def _probe(path: Path) -> tuple[int, int, float, bool]:
    def q(stream: str, entries: str) -> str:
        return subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", stream,
             "-show_entries", entries, "-of", "csv=p=0", str(path)],
            capture_output=True, text=True,
        ).stdout.strip()
    wh = q("v:0", "stream=width,height").splitlines()[0].split(",")
    rate = q("v:0", "stream=r_frame_rate") or "0/1"
    num, den = (rate.split("/") + ["1"])[:2]
    fps = (float(num) / float(den)) if float(den) else 0.0
    has_audio = bool(q("a:0", "stream=index"))
    return int(wh[0]), int(wh[1]), round(fps, 2), has_audio


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True)
    ap.add_argument("--publish-root", type=Path, default=ROOT / "output" / "meme_publish")
    args = ap.parse_args()
    folder = args.publish_root / args.series
    finals = sorted(folder.glob("*.mp4"))
    if not finals:
        print(f"нет финалов в {folder}")
        sys.exit(1)
    any_fail = False
    for f in finals:
        fails = check_format(*_probe(f))
        status = "PASS" if not fails else "FAIL: " + "; ".join(fails)
        any_fail = any_fail or bool(fails)
        print(f"[{status}] {f.name}")
    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
