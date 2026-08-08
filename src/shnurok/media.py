"""HDR->SDR tonemap detection for shnurok source clips.

iPhone footage (talking-head AND b-roll) can be shot in 10-bit HLG
(`arib-std-b67`) or PQ (`smpte2084`) instead of SDR bt709. Every shnurok cut
is encoded and tagged as bt709 without checking this — on an HDR source that
produces washed/flat output (the color values are reinterpreted, not
tonemapped). `source_tonemap_prefix` gives the render call sites a filter
prefix to prepend to their scale chain: a real tonemap for HDR sources, or
an empty string (no-op) for SDR ones.
"""
from __future__ import annotations
import subprocess
from pathlib import Path

HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}


def video_transfer(path) -> str:
    """color_transfer of a video's first stream ('' if unknown)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=color_transfer", "-of", "default=nw=1:nk=1",
                        str(path)], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def hdr_tonemap_prefix(transfer: str) -> str:
    """ffmpeg filter prefix (ending in ',') that tonemaps HDR->SDR bt709, or '' for SDR.
    Prepend to a scale chain. Empty when the source is not HDR."""
    if transfer in HDR_TRANSFERS:
        return ("zscale=t=linear:npl=100,tonemap=tonemap=hable:desat=0,"
                "zscale=t=bt709:m=bt709:r=tv,")
    return ""


def source_tonemap_prefix(path) -> str:
    return hdr_tonemap_prefix(video_transfer(path))
