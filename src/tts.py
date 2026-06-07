from __future__ import annotations
import hashlib
import os
import subprocess
import time
from pathlib import Path
import shutil
import tempfile

from dotenv import load_dotenv
from elevenlabs import (
    ElevenLabs,
    PronunciationDictionaryVersionLocator,
    VoiceSettings,
)
from rich.console import Console

from .schemas import TTSConfig
from .tts_text import split_product_breaks

load_dotenv()
console = Console()

_FINAL_TRIM_WINDOW_SEC = 0.02
_FINAL_TRIM_MIN_VOICE_SEC = 0.06
_FINAL_TRIM_KEEP_SILENCE_SEC = 0.015
_FINAL_EDGE_FADE_SEC = 0.005
_DEFAULT_MP3_BITRATE = "192k"
_TTS_CACHE_VERSION = "v9_direct_ui_like"
_TTS_RENDER_ATTEMPTS = 3
_TTS_RETRY_BASE_DELAY_SEC = 1.0


def _text_hash(
    text: str,
    settings: TTSConfig | None,
    tail_silence_sec: float,
    product_break_pause_sec: float,
    lead_silence_sec: float = 0.0,
) -> str:
    payload = f"{_TTS_CACHE_VERSION}|{text}"
    if settings is not None:
        dictionary_payload = ",".join(
            f"{locator.pronunciation_dictionary_id}:{locator.version_id}"
            for locator in settings.pronunciation_dictionary_locators
        )
        payload += (
            f"|{settings.model_id}|{settings.output_format}"
            f"|{settings.apply_text_normalization}|{settings.generation_mode}"
            f"|markup={settings.markup_dialect}|{settings.language_code}"
            f"|speed={settings.speed}|dict={dictionary_payload}"
        )
    payload += f"|tail={tail_silence_sec}|product_break_pause={product_break_pause_sec}|lead={lead_silence_sec}"
    return hashlib.md5(payload.encode()).hexdigest()[:8]


def _pad_with_silence(mp3_path: Path, silence_sec: float) -> None:
    """Дописывает `silence_sec` секунд тишины в конец mp3 (in-place)."""
    if silence_sec <= 0:
        return
    tmp = mp3_path.with_suffix(".padded.mp3")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(mp3_path),
        "-f", "lavfi", "-t", f"{silence_sec}", "-i", "anullsrc=r=44100:cl=mono",
        "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1",
        "-c:a", "libmp3lame", "-b:a", _DEFAULT_MP3_BITRATE,
        str(tmp),
    ]
    subprocess.run(cmd, check=True)
    tmp.replace(mp3_path)


def _prepend_silence(mp3_path: Path, silence_sec: float) -> None:
    """Add `silence_sec` seconds of controlled silence to the start of an mp3."""
    if silence_sec <= 0:
        return
    sample_rate, _, channel_layout = _probe_audio_params(mp3_path)
    tmp = mp3_path.with_suffix(".leadpadded.mp3")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-t", f"{silence_sec:.3f}",
        "-i", f"anullsrc=r={sample_rate}:cl={channel_layout}",
        "-i", str(mp3_path),
        "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1",
        "-c:a", "libmp3lame", "-b:a", _DEFAULT_MP3_BITRATE,
        str(tmp),
    ]
    subprocess.run(cmd, check=True)
    tmp.replace(mp3_path)


def _probe_audio_params(audio_path: Path) -> tuple[int, int, str]:
    """Return sample rate, channels, and a simple channel layout for ffmpeg."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate,channels",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    try:
        sample_rate = int(lines[0])
    except (IndexError, ValueError):
        sample_rate = 44100

    try:
        channels = int(lines[1])
    except (IndexError, ValueError):
        channels = 1

    channel_layout = "stereo" if channels >= 2 else "mono"
    return sample_rate, channels, channel_layout


def _probe_duration(audio_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _trim_final_leading_silence(audio_path: Path) -> None:
    """Remove synthetic leading silence from the final TTS render in-place.

    ElevenLabs direct renders often prepend ~100-200 ms of dead air. If we keep
    it, both the spoken onset and aligned subtitles feel late against the first
    b-roll frame. We shave only the very start and intentionally keep a tiny
    buffer so the first consonant is not clipped.
    """
    sample_rate, channels, _ = _probe_audio_params(audio_path)
    trimmed_path = audio_path.with_suffix(".leadtrim.wav")
    threshold = "-50dB"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(audio_path),
        "-af",
        (
            "silenceremove="
            f"start_periods=1:start_duration={_FINAL_TRIM_MIN_VOICE_SEC}:"
            f"start_threshold={threshold}:"
            f"start_silence={_FINAL_TRIM_KEEP_SILENCE_SEC}:"
            f"start_mode=all:detection=rms:window={_FINAL_TRIM_WINDOW_SEC},"
            f"afade=t=in:st=0:d={_FINAL_EDGE_FADE_SEC}"
        ),
        "-c:a", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", str(channels),
        str(trimmed_path),
    ]
    subprocess.run(cmd, check=True)

    remuxed_path = audio_path.with_suffix(".leadtrim.mp3")
    remux_cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(trimmed_path),
        "-c:a", "libmp3lame",
        "-b:a", _DEFAULT_MP3_BITRATE,
        str(remuxed_path),
    ]
    subprocess.run(remux_cmd, check=True)
    remuxed_path.replace(audio_path)
    trimmed_path.unlink(missing_ok=True)


def _pronunciation_dictionary_locators(
    settings: TTSConfig | None,
) -> list[PronunciationDictionaryVersionLocator]:
    if settings is None:
        return []
    return [
        PronunciationDictionaryVersionLocator(
            pronunciation_dictionary_id=locator.pronunciation_dictionary_id,
            version_id=locator.version_id,
        )
        for locator in settings.pronunciation_dictionary_locators
    ]


def _tts_request_kwargs(
    *,
    voice_id: str,
    text: str,
    settings: TTSConfig | None,
) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "voice_id": voice_id,
        "text": text,
        "model_id": settings.model_id if settings is not None else "eleven_multilingual_v2",
        "output_format": settings.output_format if settings is not None else "mp3_44100_128",
    }
    if settings is not None and settings.language_code is not None:
        kwargs["language_code"] = settings.language_code
        kwargs["apply_text_normalization"] = settings.apply_text_normalization
    pronunciation_dictionary_locators = _pronunciation_dictionary_locators(settings)
    if pronunciation_dictionary_locators:
        kwargs["pronunciation_dictionary_locators"] = pronunciation_dictionary_locators
    if settings is not None and settings.speed != 1.0:
        kwargs["voice_settings"] = VoiceSettings(speed=settings.speed)
    return kwargs


def _write_tts_stream(chunks, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(f"{out_path.name}.tmp")
    try:
        with open(tmp_path, "wb") as f:
            for chunk in chunks:
                f.write(chunk)
        if tmp_path.stat().st_size == 0:
            raise RuntimeError("TTS stream produced no audio bytes")
        tmp_path.replace(out_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _concat_audio_sequence(
    chunk_paths: list[Path],
    pauses_after_sec: list[float],
    out_path: Path,
) -> None:
    if not chunk_paths:
        raise ValueError("No chunk audio files to concatenate")
    if len(chunk_paths) == 1:
        shutil.copyfile(chunk_paths[0], out_path)
        return

    sample_rate, _, channel_layout = _probe_audio_params(chunk_paths[0])
    cmd = ["ffmpeg", "-y"]
    filter_labels: list[str] = []
    input_idx = 0

    for idx, chunk_path in enumerate(chunk_paths):
        cmd += ["-i", str(chunk_path)]
        filter_labels.append(f"[{input_idx}:a]")
        input_idx += 1
        if idx < len(pauses_after_sec) and pauses_after_sec[idx] > 0:
            cmd += [
                "-f", "lavfi",
                "-t", f"{pauses_after_sec[idx]:.3f}",
                "-i", f"anullsrc=r={sample_rate}:cl={channel_layout}",
            ]
            filter_labels.append(f"[{input_idx}:a]")
            input_idx += 1

    cmd += [
        "-filter_complex",
        f"{''.join(filter_labels)}concat=n={len(filter_labels)}:v=0:a=1[aout]",
        "-map", "[aout]",
        "-c:a", "libmp3lame",
        "-b:a", _DEFAULT_MP3_BITRATE,
        str(out_path),
    ]
    subprocess.run(cmd, check=True)


def _render_text_segment(
    client: ElevenLabs,
    voice_id: str,
    text: str,
    out_path: Path,
    settings: TTSConfig | None,
) -> None:
    for attempt in range(1, _TTS_RENDER_ATTEMPTS + 1):
        try:
            stream = client.text_to_speech.convert(
                **_tts_request_kwargs(voice_id=voice_id, text=text, settings=settings)
            )
            _write_tts_stream(stream, out_path)
            return
        except Exception:
            if attempt >= _TTS_RENDER_ATTEMPTS:
                raise
            delay_sec = _TTS_RETRY_BASE_DELAY_SEC * attempt
            console.log(
                f"[yellow]→[/yellow] TTS stream failed, retrying "
                f"({attempt + 1}/{_TTS_RENDER_ATTEMPTS}) in {delay_sec:.1f}s"
            )
            time.sleep(delay_sec)


def _synthesize_with_product_breaks(
    client: ElevenLabs,
    voice_id: str,
    text: str,
    out_path: Path,
    settings: TTSConfig | None,
    product_break_pause_sec: float,
) -> None:
    segments = split_product_breaks(text)
    if len(segments) <= 1:
        _render_text_segment(client, voice_id, text, out_path, settings)
        return

    console.log(
        f"[blue]→[/blue] Synthesizing {len(segments)} product-break segment(s) "
        f"with {product_break_pause_sec:.1f}s insert pause"
    )
    with tempfile.TemporaryDirectory(prefix="tts_product_break_") as tmp_dir:
        tmp_root = Path(tmp_dir)
        segment_paths: list[Path] = []
        for idx, segment in enumerate(segments):
            segment_path = tmp_root / f"segment_{idx:02d}.mp3"
            _render_text_segment(
                client,
                voice_id,
                segment,
                segment_path,
                settings,
            )
            segment_paths.append(segment_path)
        _concat_audio_sequence(
            segment_paths,
            [product_break_pause_sec] * max(0, len(segment_paths) - 1),
            out_path,
        )


def synthesize(
    text: str,
    out_path: Path,
    settings: TTSConfig | None = None,
    tail_silence_sec: float = 0.0,
    product_break_pause_sec: float = 0.0,
    lead_silence_sec: float = 0.0,
) -> Path:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID")
    if not api_key:
        raise EnvironmentError("ELEVENLABS_API_KEY not set")
    if not voice_id:
        raise EnvironmentError("ELEVENLABS_VOICE_ID not set")

    # idempotency: skip if file exists and a hash sidecar matches
    hash_file = out_path.with_suffix(".hash")
    current_hash = _text_hash(
        text,
        settings,
        tail_silence_sec,
        product_break_pause_sec,
        lead_silence_sec,
    )
    if out_path.exists() and hash_file.exists() and hash_file.read_text().strip() == current_hash:
        console.log(f"[yellow]→[/yellow] TTS cache hit, skipping synthesis")
        return out_path

    console.log(f"[blue]→[/blue] Synthesizing voiceover ({len(text)} chars)...")
    client = ElevenLabs(api_key=api_key)
    if len(split_product_breaks(text)) > 1:
        _synthesize_with_product_breaks(
            client,
            voice_id,
            text,
            out_path,
            settings,
            product_break_pause_sec,
        )
    else:
        _render_text_segment(client, voice_id, text, out_path, settings)

    _trim_final_leading_silence(out_path)
    console.log("[blue]→[/blue] Trimmed synthetic leading silence from voiceover")
    if lead_silence_sec > 0:
        _prepend_silence(out_path, lead_silence_sec)
        console.log(f"[blue]→[/blue] Padded voiceover with {lead_silence_sec}s lead silence")
    if tail_silence_sec > 0:
        _pad_with_silence(out_path, tail_silence_sec)
        console.log(f"[blue]→[/blue] Padded voiceover with {tail_silence_sec}s tail silence")

    hash_file.write_text(current_hash)
    console.log(f"[green]✓[/green] Voiceover saved: {out_path}")
    return out_path
