"""Per-asset transcription: extract audio from hook/CTA video, run Whisper once,
store transcript as sidecar JSON next to the video file."""
from __future__ import annotations
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Literal

from rich.console import Console

from .schemas import Transcript
from .transcribe import align_to_text, transcribe

console = Console()

AssetType = Literal["hooks", "ctas"]


def transcript_sidecar_path(video_path: Path) -> Path:
    """Path to the cached transcript JSON beside the asset video file."""
    return video_path.with_suffix(".transcript.json")


def transcript_hash_path(video_path: Path) -> Path:
    """Hash sidecar for invalidating stale transcripts when the asset file changes."""
    return transcript_sidecar_path(video_path).with_suffix(".json.hash")


def _asset_signature(
    video_path: Path,
    model_size: str,
    language: str,
    transcript_text: str | None,
) -> str:
    stat = video_path.stat()
    alignment_mode = "forced" if transcript_text else "free"
    payload = (
        f"{stat.st_size}:{stat.st_mtime_ns}:{model_size}:{language}:"
        f"{alignment_mode}:{transcript_text or ''}"
    )
    return hashlib.md5(payload.encode()).hexdigest()[:12]


def _extract_audio(video_path: Path, out_audio: Path) -> Path:
    """Extract mono 16kHz wav from video — optimal input for Whisper."""
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000",
        "-f", "wav", str(out_audio),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"audio extraction failed:\n{result.stderr}")
    return out_audio


def transcribe_asset(
    video_path: Path,
    model_size: str = "medium",
    language: str = "ru",
    transcript_text: str | None = None,
) -> Transcript:
    """Transcribe a hook/CTA video; cache as JSON sidecar. Idempotent."""
    sidecar = transcript_sidecar_path(video_path)
    hash_sidecar = transcript_hash_path(video_path)
    current_hash = _asset_signature(video_path, model_size, language, transcript_text)

    if (
        sidecar.exists()
        and hash_sidecar.exists()
        and hash_sidecar.read_text().strip() == current_hash
    ):
        console.log(f"[yellow]→[/yellow] Asset transcript cache hit: {sidecar.name}")
        return Transcript(**json.loads(sidecar.read_text()))

    if sidecar.exists():
        console.log(
            f"[yellow]→[/yellow] Asset transcript cache stale: {sidecar.name} "
            f"(asset/model changed, regenerating)"
        )
        sidecar.unlink(missing_ok=True)
    hash_sidecar.unlink(missing_ok=True)

    # extract audio to a temp wav next to the video
    tmp_audio = video_path.with_suffix(".extracted.wav")
    try:
        _extract_audio(video_path, tmp_audio)
        if transcript_text:
            transcript = align_to_text(
                tmp_audio,
                transcript_text,
                sidecar,
                language=language,
                model_size=model_size,
                display_text=transcript_text,
            )
        else:
            transcript = transcribe(tmp_audio, sidecar, language=language, model_size=model_size)
    finally:
        tmp_audio.unlink(missing_ok=True)
    hash_sidecar.write_text(current_hash)
    return transcript
