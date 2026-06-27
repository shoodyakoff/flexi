from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from talking_head_retake_planner import (
        TimedRange,
        apply_retake_decisions,
        build_boundary_phrase_restart_decisions,
        build_retake_decisions,
        merge_retake_decisions,
        normalize_token,
        phrase_attempts_from_transcript,
        plan_retakes_from_decisions,
    )
except ModuleNotFoundError:
    from pipelines.talking_head_retake_planner import (
        TimedRange,
        apply_retake_decisions,
        build_boundary_phrase_restart_decisions,
        build_retake_decisions,
        merge_retake_decisions,
        normalize_token,
        phrase_attempts_from_transcript,
        plan_retakes_from_decisions,
    )

from src.output_paths import latest_version_dir, seed_reusable, versioned_dir

CONFIG_PATH = ROOT / "config.yaml"
OUTPUT_ROOT = ROOT / "output"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv"}

W = 1080
H = 1920
FPS = 30
TALK_SHIFT_UP = 360
BROLL_H = 680
BROLL_Y = H - BROLL_H
HORIZONTAL_MEDIUM_Y = 260
CANVAS_BACKGROUND = "0x101010"

COLOR_TAGS = [
    "-color_range",
    "tv",
    "-colorspace",
    "bt709",
    "-color_primaries",
    "bt709",
    "-color_trc",
    "bt709",
    "-pix_fmt",
    "yuv420p",
]
X264_COLOR_PARAMS = "colorprim=bt709:colormatrix=bt709:transfer=bt709"
MICRO_PUSH_SCALE = 0.0
CLOSE_SCALE = 1.20
TECHNICAL_NORMALIZE = (
    f"fps={FPS},zscale=t=linear:npl=1000,format=gbrpf32le,"
    "zscale=p=bt709,tonemap=tonemap=mobius:desat=0,"
    "zscale=t=bt709:m=bt709:r=limited,format=yuv420p,"
)


@dataclass(frozen=True)
class SpeechSegment:
    source_index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class EditChunk:
    index: int
    source_index: int
    source: str
    start: float
    end: float
    duration: float
    plan: str
    transition: str
    broll: str | None = None
    video_transform: str | None = None


def run(cmd: list[str], label: str, *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    print(f"-> {label}", flush=True)
    result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    if capture:
        return result
    return result


def ffprobe_duration(path: Path) -> float:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        f"probe duration {path.name}",
        capture=True,
    )
    return float(result.stdout.strip())


def probe_rotation(path: Path) -> int:
    """Display rotation (degrees) from the clip's rotation side-data, or 0."""
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream_side_data=rotation",
            "-of",
            "json",
            str(path),
        ],
        f"probe rotation {path.name}",
        capture=True,
    )
    try:
        streams = json.loads(result.stdout).get("streams", [])
        for side_data in streams[0].get("side_data_list", []) if streams else []:
            if "rotation" in side_data:
                return int(float(side_data["rotation"]))
    except (json.JSONDecodeError, ValueError, IndexError, KeyError):
        return 0
    return 0


def log_source_orientation(sources: list[Path]) -> None:
    """Print the per-clip rotation table.

    Each source is auto-rotated independently at render time, so mixed
    rotations across the day's clips are handled correctly — this table makes
    that auditable (and surfaces the footgun of pre-concatenating clips with
    differing rotation, which would force a single rotation onto all of them).
    """
    print("-> talking-head source orientation:", flush=True)
    rotations = set()
    for index, source in enumerate(sources):
        rotation = probe_rotation(source)
        rotations.add(rotation % 360)
        upright = "portrait" if abs(rotation) % 180 == 90 else "as-shot"
        print(f"     [{index}] {source.name}: rotation={rotation:>4}  ({upright})", flush=True)
    if len(rotations) > 1:
        print(
            "     note: clips have MIXED rotations — each is auto-rotated per source, "
            "so do not pre-concatenate them (stream-copy concat would flip the odd ones).",
            flush=True,
        )


def source_is_landscape(path: Path) -> bool:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:stream_side_data=rotation",
            "-of",
            "json",
            str(path),
        ],
        f"probe display orientation {path.name}",
        capture=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    width = int(stream["width"])
    height = int(stream["height"])
    rotation = 0
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            rotation = int(float(side_data["rotation"]))
            break
    if abs(rotation) % 180 == 90:
        width, height = height, width
    return width >= height


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def detect_silences(path: Path, threshold_db: float, min_silence: float) -> list[tuple[float, float | None]]:
    sample_rate = 16000
    frame_sec = 0.03
    merge_gap_sec = 0.12
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "s16le",
            "-",
        ],
        cwd=ROOT,
        text=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"audio analysis failed for {path}\n{result.stderr.decode(errors='replace')}")

    pcm = result.stdout
    frame_bytes = int(frame_sec * sample_rate) * 2
    total_duration = len(pcm) / 2 / sample_rate
    silent_flags: list[bool] = []
    for start in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
        frame = pcm[start : start + frame_bytes]
        # RMS of signed 16-bit PCM (replaces stdlib audioop, removed in Python 3.13).
        samples = np.frombuffer(frame, dtype=np.int16)
        rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))) if samples.size else 0.0
        db = -100.0 if rms <= 0 else 20 * math.log10(rms / 32768)
        silent_flags.append(db < threshold_db)

    raw: list[tuple[float, float]] = []
    start_index: int | None = None
    for index, is_silent in enumerate([*silent_flags, False]):
        if is_silent and start_index is None:
            start_index = index
        if not is_silent and start_index is not None:
            start_sec = start_index * frame_sec
            end_sec = min(index * frame_sec, total_duration)
            if end_sec - start_sec >= min_silence:
                raw.append((start_sec, end_sec))
            start_index = None

    merged: list[tuple[float, float]] = []
    for start_sec, end_sec in raw:
        if merged and start_sec - merged[-1][1] <= merge_gap_sec:
            merged[-1] = (merged[-1][0], end_sec)
        else:
            merged.append((start_sec, end_sec))

    return [(start_sec, end_sec) for start_sec, end_sec in merged if end_sec - start_sec >= min_silence]


def speech_from_silences(
    *,
    source_index: int,
    duration: float,
    silences: list[tuple[float, float | None]],
    pad: float,
    min_keep: float,
) -> list[SpeechSegment]:
    speech: list[SpeechSegment] = []
    cursor = 0.0
    for silence_start, silence_end in silences:
        end = max(0.0, silence_start + pad)
        if end - cursor >= min_keep:
            speech.append(SpeechSegment(source_index, max(0.0, cursor), min(duration, end)))
        cursor = min(duration, (silence_end if silence_end is not None else duration) - pad)

    if duration - cursor >= min_keep:
        speech.append(SpeechSegment(source_index, max(0.0, cursor), duration))
    return speech


def split_segment(segment: SpeechSegment, target: float, max_len: float, min_len: float) -> list[SpeechSegment]:
    if segment.duration <= max_len:
        return [segment]

    count = max(1, round(segment.duration / target))
    while segment.duration / count > max_len:
        count += 1
    step = segment.duration / count
    chunks: list[SpeechSegment] = []
    cursor = segment.start
    for index in range(count):
        end = segment.end if index == count - 1 else segment.start + step * (index + 1)
        if end - cursor >= min_len:
            chunks.append(SpeechSegment(segment.source_index, cursor, end))
        elif chunks:
            prev = chunks[-1]
            chunks[-1] = SpeechSegment(prev.source_index, prev.start, end)
        cursor = end
    return chunks


def warn_on_large_retake_drops(
    before: list[SpeechSegment],
    after: list[SpeechSegment],
    sources: list[Path],
    *,
    threshold_sec: float = 4.0,
) -> None:
    """Warn when retake removal drops a long contiguous span of kept speech.

    `smart` retake mode occasionally mis-reads a genuine continuous thought as a
    false start and removes several seconds of real narrative, leaving a jump
    mid-sentence. This is read-only — it does not change the cut — but flags the
    span so it can be reviewed (and re-rendered with --retake-mode off or an
    explicit --edit-decisions-json if wrong).
    """
    def coverage(segments: list[SpeechSegment], source_index: int) -> list[tuple[float, float]]:
        spans = sorted(
            (seg.start, seg.end) for seg in segments if seg.source_index == source_index
        )
        merged: list[tuple[float, float]] = []
        for start, end in spans:
            if merged and start <= merged[-1][1] + 1e-3:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    for source_index, source in enumerate(sources):
        kept_after = coverage(after, source_index)
        for start, end in coverage(before, source_index):
            cursor = start
            for a_start, a_end in kept_after:
                if a_end <= cursor or a_start >= end:
                    continue
                if a_start - cursor >= threshold_sec:
                    print(
                        f"     ⚠ retake removal dropped {a_start - cursor:.1f}s of speech in "
                        f"{source.name} [{cursor:.1f}-{a_start:.1f}] — review if a real sentence "
                        f"was cut (re-run with --retake-mode off or --edit-decisions-json).",
                        flush=True,
                    )
                cursor = max(cursor, a_end)
            if end - cursor >= threshold_sec:
                print(
                    f"     ⚠ retake removal dropped {end - cursor:.1f}s of speech in "
                    f"{source.name} [{cursor:.1f}-{end:.1f}] — review if a real sentence "
                    f"was cut (re-run with --retake-mode off or --edit-decisions-json).",
                    flush=True,
                )


def collect_broll(paths: list[Path], broll_dir: Path | None) -> list[Path]:
    brolls = [path for path in paths if path.suffix.lower() in VIDEO_EXTENSIONS]
    if broll_dir:
        brolls.extend(
            path
            for path in sorted(broll_dir.iterdir())
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        )
    return brolls


def choose_plan(index: int, brolls: list[Path], *, landscape_source: bool) -> tuple[str, Path | None]:
    if brolls and index % 3 == 2:
        return "medium_with_broll", brolls[index % len(brolls)]
    if index % 3 == 1:
        if landscape_source:
            return "medium_close", None
        return "close", None
    return "medium", None


def make_chunks(
    sources: list[Path],
    speech: list[SpeechSegment],
    brolls: list[Path],
    target_len: float,
    max_len: float,
    min_len: float,
    transition: str,
    split_long_speech: bool,
) -> list[EditChunk]:
    visual_beats: list[SpeechSegment] = []
    for segment in speech:
        if split_long_speech:
            visual_beats.extend(split_segment(segment, target_len, max_len, min_len))
        else:
            visual_beats.append(segment)

    landscape_sources = [source_is_landscape(path) for path in sources]
    chunks: list[EditChunk] = []
    for index, beat in enumerate(visual_beats):
        plan, broll = choose_plan(
            index,
            brolls,
            landscape_source=landscape_sources[beat.source_index],
        )
        chunks.append(
            EditChunk(
                index=index,
                source_index=beat.source_index,
                source=display_path(sources[beat.source_index]),
                start=round(beat.start, 3),
                end=round(beat.end, 3),
                duration=round(beat.duration, 3),
                plan=plan,
                transition=transition,
                broll=display_path(broll) if broll else None,
            )
        )
    return chunks


def load_decision_chunks(path: Path) -> list[EditChunk]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_chunks = data["chunks"] if isinstance(data, dict) else data
    chunks: list[EditChunk] = []
    for index, raw in enumerate(raw_chunks):
        start = float(raw["start"])
        end = float(raw["end"])
        duration = float(raw.get("duration", end - start))
        chunks.append(
            EditChunk(
                index=int(raw.get("index", index)),
                source_index=int(raw["source_index"]),
                source=str(raw.get("source", "")),
                start=round(start, 3),
                end=round(end, 3),
                duration=round(duration, 3),
                plan=str(raw["plan"]),
                transition=str(raw.get("transition", "xfade")),
                broll=raw.get("broll"),
                video_transform=raw.get("video_transform"),
            )
        )
    return chunks


def zoom_expr(chunk: EditChunk, *, base_scale: float) -> str:
    if chunk.index == 0 or MICRO_PUSH_SCALE <= 0:
        return f"{base_scale:.5f}"
    return f"({base_scale:.5f})*(1+{MICRO_PUSH_SCALE:.5f}*max(0\\,1-t/0.180))"


def cover_crop_filter(src: str, label: str, chunk: EditChunk, *, scale: float = 1.0) -> str:
    zoom = zoom_expr(chunk, base_scale=scale)
    return (
        f"{src}{TECHNICAL_NORMALIZE}"
        f"scale=w='{W}*{zoom}':h='{H}*{zoom}':"
        "force_original_aspect_ratio=increase:eval=frame,"
        f"crop={W}:{H},setsar=1,format=yuv420p{label}"
    )


def medium_filter(src: str, label: str, chunk: EditChunk, *, y: str, scale: float = 1.0) -> str:
    zoom = zoom_expr(chunk, base_scale=scale)
    fg_src = f"[fgsrc{chunk.index}]"
    bg = f"[bg{chunk.index}]"
    fg = f"[fg{chunk.index}]"
    return (
        f"color=c={CANVAS_BACKGROUND}:s={W}x{H}:r={FPS}:d={chunk.duration:.3f},format=yuv420p{bg};"
        f"{src}{TECHNICAL_NORMALIZE}format=yuv420p{fg_src};"
        f"{fg_src}scale=w='{W}*{zoom}':h='{H}*{zoom}':"
        "force_original_aspect_ratio=decrease:eval=frame,"
        f"setsar=1,format=yuv420p{fg};"
        f"{bg}{fg}overlay=x=(W-w)/2:y={y}:shortest=1{label}"
    )


def chunk_video_filter(chunk: EditChunk, broll_input_index: int | None, label: str) -> str:
    src = f"[{chunk.source_index}:v]trim=start={chunk.start:.3f}:end={chunk.end:.3f},setpts=PTS-STARTPTS,"
    if chunk.video_transform == "rotate180":
        src += "hflip,vflip,"
    elif chunk.video_transform:
        raise ValueError(f"Unsupported video_transform: {chunk.video_transform}")
    if chunk.plan == "close":
        return cover_crop_filter(src, label, chunk, scale=CLOSE_SCALE)
    if chunk.plan == "medium_close":
        return medium_filter(src, label, chunk, y="(H-h)/2", scale=CLOSE_SCALE)
    if chunk.plan != "medium_with_broll" or broll_input_index is None:
        return medium_filter(src, label, chunk, y="(H-h)/2")

    talk = f"[talk{chunk.index}]"
    broll = f"[broll{chunk.index}]"
    shifted = f"[shifted{chunk.index}]"
    y = f"if(gte(h\\,{H})\\,-{TALK_SHIFT_UP}\\,{HORIZONTAL_MEDIUM_Y})"
    return (
        medium_filter(src, talk, chunk, y=y)
        + ";"
        f"[{broll_input_index}:v]trim=duration={chunk.duration:.3f},setpts=PTS-STARTPTS,"
        f"{TECHNICAL_NORMALIZE}scale={W}:{BROLL_H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{BROLL_H},setsar=1,format=yuv420p{broll};"
        f"{talk}{broll}overlay=x=0:y={BROLL_Y}:shortest=1{shifted};"
        f"{shifted}format=yuv420p{label}"
    )


def chunk_audio_filter(chunk: EditChunk, label: str) -> str:
    fade_out = max(0.0, chunk.duration - 0.025)
    return (
        f"[{chunk.source_index}:a]atrim=start={chunk.start:.3f}:end={chunk.end:.3f},"
        "asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo,"
        f"afade=t=in:st=0:d=0.025,afade=t=out:st={fade_out:.3f}:d=0.025{label}"
    )


def render_chunk(sources: list[Path], chunk: EditChunk, clips_dir: Path) -> Path:
    out = clips_dir / f"chunk_{chunk.index:03d}_{chunk.plan}.mp4"
    cmd = ["ffmpeg", "-y"]
    cmd.extend(
        [
            "-ss",
            f"{chunk.start:.3f}",
            "-t",
            f"{chunk.duration:.3f}",
            "-i",
            str(sources[chunk.source_index]),
        ]
    )
    broll_input_index = None
    if chunk.plan == "medium_with_broll" and chunk.broll:
        broll_input_index = 1
        cmd.extend(["-stream_loop", "-1", "-i", chunk.broll])

    local_chunk = EditChunk(
        index=chunk.index,
        source_index=0,
        source=chunk.source,
        start=0.0,
        end=chunk.duration,
        duration=chunk.duration,
        plan=chunk.plan,
        transition=chunk.transition,
        broll=chunk.broll,
        video_transform=chunk.video_transform,
    )
    filters = [
        chunk_video_filter(local_chunk, broll_input_index, "[vout]"),
        chunk_audio_filter(local_chunk, "[aout]"),
    ]
    filter_complex = ";".join(filters)
    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-r",
            str(FPS),
            "-x264-params",
            X264_COLOR_PARAMS,
            *COLOR_TAGS,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(out),
        ]
    )
    run(cmd, f"render chunk {chunk.index:03d} {chunk.plan}")
    return out


def concat_chunks(chunk_paths: list[Path], chunks: list[EditChunk], out_dir: Path, transition_duration: float) -> Path:
    out = out_dir / "final_clean.mp4"
    concat_file = out_dir / "concat.txt"
    concat_file.write_text(
        "".join(f"file '{path.resolve()}'\n" for path in chunk_paths),
        encoding="utf-8",
    )
    if len(chunk_paths) > 1 and transition_duration > 0:
        cmd = ["ffmpeg", "-y"]
        for path in chunk_paths:
            cmd.extend(["-i", str(path)])

        filters: list[str] = []
        for index in range(len(chunk_paths)):
            filters.append(f"[{index}:v]setpts=PTS-STARTPTS,format=yuv420p[v{index}]")
            filters.append(
                f"[{index}:a]asetpts=PTS-STARTPTS,"
                "aformat=sample_rates=48000:channel_layouts=stereo"
                f"[a{index}]"
            )

        video_label = "v0"
        audio_label = "a0"
        timeline = chunks[0].duration
        for index in range(1, len(chunk_paths)):
            duration = effective_transition_duration(chunks[index - 1], chunks[index], transition_duration)
            offset = max(0.0, timeline - duration)
            next_video_label = f"vx{index}"
            next_audio_label = f"ax{index}"
            filters.append(
                f"[{video_label}][v{index}]"
                f"xfade=transition=fade:duration={duration:.3f}:offset={offset:.3f}"
                f"[{next_video_label}]"
            )
            filters.append(
                f"[{audio_label}][a{index}]"
                f"acrossfade=d={duration:.3f}:c1=tri:c2=tri"
                f"[{next_audio_label}]"
            )
            video_label = next_video_label
            audio_label = next_audio_label
            timeline += chunks[index].duration - duration

        cmd.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                f"[{video_label}]",
                "-map",
                f"[{audio_label}]",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-r",
                str(FPS),
                "-x264-params",
                X264_COLOR_PARAMS,
                *COLOR_TAGS,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(out),
            ]
        )
        run(cmd, "xfade dynamic clean chunks")
        return out

    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(out),
        ],
        "concat dynamic clean chunks",
    )
    return out


def render(sources: list[Path], chunks: list[EditChunk], out_dir: Path, transition_duration: float) -> Path:
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    chunk_paths = [render_chunk(sources, chunk, clips_dir) for chunk in chunks]
    return concat_chunks(chunk_paths, chunks, out_dir, transition_duration)


def effective_transition_duration(previous: EditChunk, current: EditChunk, requested_duration: float) -> float:
    if requested_duration <= 0:
        return 0.0
    return min(
        requested_duration,
        max(0.04, previous.duration * 0.33),
        max(0.04, current.duration * 0.33),
    )


def retake_review_path(args: argparse.Namespace, out_dir: Path) -> Path:
    if args.retake_review_json is None:
        return out_dir / "retake_decisions.json"
    if args.retake_review_json.is_absolute():
        return args.retake_review_json
    return ROOT / args.retake_review_json


def talking_head_source_archive_dir(slug: str, *, root: Path = ROOT) -> Path:
    return root / "assets" / "talking_head_sources" / slug


def root_transcript_sidecars(source: Path) -> list[Path]:
    return [
        source.with_name(f"{source.stem}.transcript.json"),
        source.with_name(f"{source.stem}.transcript.json.hash"),
    ]


def _move_without_overwrite(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing source artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.replace(destination)


def organize_talking_head_sources(sources: list[Path], *, slug: str, root: Path = ROOT) -> list[Path]:
    archive_dir = talking_head_source_archive_dir(slug, root=root)
    organized: list[Path] = []
    for source in sources:
        if source.parent != root:
            organized.append(source)
            continue

        archived_source = archive_dir / source.name
        if source.exists():
            _move_without_overwrite(source, archived_source)
            for sidecar in root_transcript_sidecars(source):
                if sidecar.exists():
                    _move_without_overwrite(sidecar, archive_dir / sidecar.name)
            organized.append(archived_source)
            continue

        organized.append(archived_source if archived_source.exists() else source)
    return organized


def transcribe_source(source: Path, out_dir: Path, source_index: int, model_size: str):
    from src.transcribe import transcribe

    safe_model = model_size.replace("/", "-")
    transcript_path = out_dir / f"source_{source_index:02d}.{source.stem}.{safe_model}.transcript.json"
    return transcribe(
        source,
        transcript_path,
        language="ru",
        model_size=model_size,
    )


def transcript_sidecar_path(source: Path, out_dir: Path, source_index: int, model_size: str) -> Path:
    safe_model = model_size.replace("/", "-")
    return out_dir / f"source_{source_index:02d}.{source.stem}.{safe_model}.transcript.json"


def meaningful_word_count_in_range(transcript: object, *, start: float, end: float) -> int:
    return sum(
        1
        for word in getattr(transcript, "words", [])
        if word.start >= start - 0.001
        and word.end <= end + 0.001
        and normalize_token(word.word)
        and normalize_token(word.word) not in {"а", "э", "ээ", "эм", "мм", "м", "ну", "вот"}
    )


def extend_boundary_orphan_tails(
    decisions: list[object],
    speech_segments: list[TimedRange],
    transcripts_by_source: dict[int, object],
    *,
    max_tail_sec: float = 1.0,
) -> list[object]:
    extended: list[object] = []
    for decision in decisions:
        if getattr(decision, "status", None) != "applied" or getattr(decision, "reason", None) != "boundary_phrase_restart":
            extended.append(decision)
            continue

        updated_ranges: list[TimedRange] = []
        updated_features = dict(getattr(decision, "features", None) or {})
        changed = False
        for removed in getattr(decision, "removed_ranges", []):
            replacement = removed
            for segment in speech_segments:
                if segment.source_index != removed.source_index:
                    continue
                if not (segment.start < removed.end < segment.end):
                    continue
                tail_duration = round(segment.end - removed.end, 3)
                if tail_duration <= 0 or tail_duration > max_tail_sec:
                    continue
                transcript = transcripts_by_source.get(removed.source_index)
                tail_word_count = (
                    meaningful_word_count_in_range(transcript, start=removed.end, end=segment.end)
                    if transcript is not None
                    else 0
                )
                if tail_word_count > 0:
                    updated_features = {
                        **updated_features,
                        "orphan_tail_action": "kept_meaningful_tail",
                        "orphan_tail_start": round(removed.end, 3),
                        "orphan_tail_end": round(segment.end, 3),
                        "orphan_tail_duration": tail_duration,
                        "orphan_tail_word_count": tail_word_count,
                    }
                    continue
                replacement = TimedRange(
                    source_index=removed.source_index,
                    start=removed.start,
                    end=round(segment.end, 3),
                )
                updated_features = {
                    **updated_features,
                    "orphan_tail_action": "extended_removed_range",
                    "orphan_tail_start": round(removed.end, 3),
                    "orphan_tail_end": round(segment.end, 3),
                    "orphan_tail_duration": tail_duration,
                    "orphan_tail_word_count": tail_word_count,
                }
                changed = True
                break
            updated_ranges.append(replacement)

        extended.append(
            replace(decision, removed_ranges=updated_ranges, features=updated_features)
            if changed
            else decision
        )
    return extended


def chunks_with_output_timing(chunks: list[EditChunk], *, transition_duration: float) -> list[dict]:
    timed: list[dict] = []
    timeline = 0.0
    for index, chunk in enumerate(chunks):
        if index == 0:
            actual_transition = 0.0
            out_start = 0.0
            out_end = chunk.duration
        else:
            actual_transition = effective_transition_duration(chunks[index - 1], chunk, transition_duration)
            out_start = max(0.0, timeline - actual_transition)
            out_end = out_start + chunk.duration
        item = asdict(chunk)
        item["out_start"] = round(out_start, 3)
        item["out_end"] = round(out_end, 3)
        item["transition_duration"] = round(actual_transition, 3)
        timed.append(item)
        timeline = out_end
    return timed


def build_timeline_quality_report(
    *,
    chunks: list[EditChunk],
    transcripts_by_source: dict[int, object],
    transition_duration: float,
) -> dict:
    timed_chunks = chunks_with_output_timing(chunks, transition_duration=transition_duration)
    issues: list[dict] = []
    short_chunks: list[dict] = []
    source_transitions: list[dict] = []

    for index, chunk in enumerate(chunks):
        transcript = transcripts_by_source.get(chunk.source_index)
        word_count = (
            meaningful_word_count_in_range(transcript, start=chunk.start, end=chunk.end)
            if transcript is not None
            else None
        )
        has_source_change_before = index > 0 and chunks[index - 1].source_index != chunk.source_index
        has_source_change_after = index + 1 < len(chunks) and chunks[index + 1].source_index != chunk.source_index
        if chunk.duration < 1.0:
            short_chunk = {
                "chunk_index": chunk.index,
                "source": chunk.source,
                "source_index": chunk.source_index,
                "start": round(chunk.start, 3),
                "end": round(chunk.end, 3),
                "duration": round(chunk.duration, 3),
                "word_count": word_count,
                "near_source_transition": has_source_change_before or has_source_change_after,
            }
            short_chunks.append(short_chunk)
            if word_count == 0 and (has_source_change_before or has_source_change_after):
                issues.append(
                    {
                        "severity": "fail",
                        "code": "short_wordless_boundary_chunk",
                        **short_chunk,
                    }
                )
            elif word_count == 0:
                issues.append(
                    {
                        "severity": "warn",
                        "code": "short_wordless_chunk",
                        **short_chunk,
                    }
                )

    for index in range(1, len(chunks)):
        if chunks[index - 1].source_index == chunks[index].source_index:
            continue
        transition = {
            "from_chunk": chunks[index - 1].index,
            "to_chunk": chunks[index].index,
            "from_source": chunks[index - 1].source,
            "to_source": chunks[index].source,
            "out_start": timed_chunks[index]["out_start"],
            "transition_duration": timed_chunks[index]["transition_duration"],
        }
        source_transitions.append(transition)

    for previous, current in zip(source_transitions, source_transitions[1:]):
        gap = round(current["out_start"] - previous["out_start"], 3)
        if gap < 1.0:
            issues.append(
                {
                    "severity": "warn",
                    "code": "source_transitions_too_close",
                    "first_to_chunk": previous["to_chunk"],
                    "second_to_chunk": current["to_chunk"],
                    "gap_sec": gap,
                }
            )

    status = "pass"
    if any(issue["severity"] == "fail" for issue in issues):
        status = "fail"
    elif issues:
        status = "warn"
    return {
        "production_route": "talking_head_dynamic_clean",
        "status": status,
        "chunks": timed_chunks,
        "short_chunks": short_chunks,
        "source_transitions": source_transitions,
        "issues": issues,
    }


def load_quality_transcripts(sources: list[Path], out_dir: Path, model_size: str) -> dict[int, object]:
    from src.schemas import Transcript

    transcripts: dict[int, object] = {}
    for source_index, source in enumerate(sources):
        path = transcript_sidecar_path(source, out_dir, source_index, model_size)
        if path.exists():
            transcripts[source_index] = Transcript(**json.loads(path.read_text(encoding="utf-8")))
    return transcripts


def write_quality_report(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def load_or_transcribe_quality_transcripts(
    sources: list[Path],
    out_dir: Path,
    model_size: str,
    *,
    transcribe_missing: bool = False,
) -> dict[int, object]:
    transcripts = load_quality_transcripts(sources, out_dir, model_size)
    if not transcribe_missing:
        return transcripts
    for source_index, source in enumerate(sources):
        if source_index not in transcripts:
            transcripts[source_index] = transcribe_source(source, out_dir, source_index, model_size)
    return transcripts


def build_talking_head_timeline_transcript(
    chunks: list[EditChunk],
    transcripts_by_source: dict[int, object],
    *,
    transition_duration: float,
    corrections: dict[str, str] | None = None,
):
    from src.schemas import Transcript, Word
    from src.subtitles import apply_word_corrections, build_corrections_map

    corrections_map = build_corrections_map(corrections)
    timed_chunks = chunks_with_output_timing(chunks, transition_duration=transition_duration)
    words: list[Word] = []
    for chunk, timed_chunk in zip(chunks, timed_chunks):
        transcript = transcripts_by_source.get(chunk.source_index)
        if transcript is None:
            continue
        for word in getattr(transcript, "words", []):
            word_midpoint = (word.start + word.end) / 2
            if word_midpoint < chunk.start - 0.001 or word_midpoint >= chunk.end + 0.001:
                continue
            clipped_start = min(max(word.start, chunk.start), chunk.end)
            clipped_end = min(max(word.end, chunk.start), chunk.end)
            if clipped_end - clipped_start < 0.02:
                continue
            output_start = float(timed_chunk["out_start"]) + (clipped_start - chunk.start)
            output_end = float(timed_chunk["out_start"]) + (clipped_end - chunk.start)
            words.append(
                Word(
                    word=apply_word_corrections(word.word, corrections_map),
                    start=round(output_start, 3),
                    end=round(output_end, 3),
                )
            )

    words.sort(key=lambda word: (word.start, word.end))
    duration = round(max([0.0, *(float(item["out_end"]) for item in timed_chunks)]), 3)
    return Transcript(
        words=words,
        full_text=" ".join(word.word.strip() for word in words if word.word.strip()),
        duration=duration,
    )


def _load_render_config():
    from src.schemas import Config

    return Config(**yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")))


def _resolve_fonts_dir(cfg: object) -> Path:
    fonts_dir = Path(cfg.fonts.directory)
    if fonts_dir.is_absolute():
        return fonts_dir.resolve()
    return (ROOT / fonts_dir).resolve()


def _subtitles_filter_value(ass_path: Path, cfg: object) -> str:
    return f"subtitles={ass_path}:fontsdir={_resolve_fonts_dir(cfg)}"


def burn_subtitles(clean_video: Path, ass_path: Path, out_path: Path, cfg: object) -> Path:
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(clean_video),
            "-vf",
            _subtitles_filter_value(ass_path, cfg),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-r",
            str(FPS),
            "-x264-params",
            X264_COLOR_PARAMS,
            *COLOR_TAGS,
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(out_path),
        ],
        "burn talking-head subtitles",
    )
    return out_path


def burn_title_overlay(
    base_video: Path,
    title_clip: Path,
    out_path: Path,
    cfg: object,
    *,
    fade_out_sec: float | None = None,
) -> Path:
    """Overlay an animated challenge-title clip (text on a black background,
    pre-positioned top-left) onto the START of ``base_video`` for the title's
    own duration. The black background is keyed out and the title fades out at
    its end, so it disappears gracefully. Audio is copied untouched."""
    title_cfg = cfg.title_overlay
    fade = title_cfg.fade_out_sec if fade_out_sec is None else fade_out_sec
    fade_start = max(0.0, ffprobe_duration(title_clip) - fade)
    title_chain = (
        f"[1:v]colorkey={title_cfg.colorkey_color}:{title_cfg.colorkey_similarity}:"
        f"{title_cfg.colorkey_blend},format=rgba,"
        f"fade=t=out:st={fade_start:.3f}:d={fade:.3f}:alpha=1[ttl]"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(base_video),
            "-i",
            str(title_clip),
            "-filter_complex",
            f"{title_chain};[0:v][ttl]overlay=0:0:eof_action=pass[v]",
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-r",
            str(FPS),
            "-x264-params",
            X264_COLOR_PARAMS,
            *COLOR_TAGS,
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(out_path),
        ],
        "overlay challenge title",
    )
    return out_path


def render_talking_head_subtitles(
    *,
    clean_video: Path,
    chunks: list[EditChunk],
    transcripts_by_source: dict[int, object],
    out_dir: Path,
    args: argparse.Namespace,
) -> dict:
    from src.subtitles import transcript_to_ass

    cfg = _load_render_config()
    style_id = args.subtitle_style or cfg.edit_profile.subtitle_style
    if style_id not in cfg.subtitle_styles:
        available = ", ".join(sorted(cfg.subtitle_styles))
        raise ValueError(f"subtitle style must be one of: {available}")

    style = cfg.subtitle_styles[style_id]
    corrections = getattr(cfg, "subtitle_corrections", {}) or {}
    transcript = build_talking_head_timeline_transcript(
        chunks,
        transcripts_by_source,
        transition_duration=args.transition_duration_sec,
        corrections=corrections,
    )
    transcript_path = out_dir / "talking_head_timeline_transcript.json"
    transcript_path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")

    ass_path = out_dir / "subtitles.ass"
    caption_plan_path = out_dir / "caption_plan.json" if style.caption_mode == "editorial" else None
    layout_warnings: list[str] = []
    transcript_to_ass(
        transcript,
        style,
        ass_path,
        safe_box=cfg.subtitle_safe_box,
        cfg=cfg,
        layout_warnings=layout_warnings,
        caption_plan_path=caption_plan_path,
        section="body",
    )

    subtitled = burn_subtitles(clean_video, ass_path, out_dir / "final_subtitled.mp4", cfg)
    return {
        "enabled": True,
        "style": style_id,
        "ass": display_path(ass_path),
        "caption_plan": display_path(caption_plan_path) if caption_plan_path is not None else None,
        "timeline_transcript": display_path(transcript_path),
        "warnings": layout_warnings,
        "clean_final": display_path(clean_video),
        "final": display_path(subtitled),
    }


def plan_speech_with_retakes(
    *,
    sources: list[Path],
    speech: list[SpeechSegment],
    out_dir: Path,
    args: argparse.Namespace,
) -> tuple[list[SpeechSegment], list[dict]]:
    if args.retake_mode == "off":
        return speech, [
            {
                "source": display_path(source),
                "source_index": source_index,
                "mode": "off",
                "decisions": [],
                "summary": {"applied": 0, "needs_review": 0, "ignored": 0},
            }
            for source_index, source in enumerate(sources)
        ]

    planned: list[SpeechSegment] = []
    reviews: list[dict] = []
    transcripts_by_source: dict[int, object] = {}
    for source_index, source in enumerate(sources):
        source_speech = [segment for segment in speech if segment.source_index == source_index]
        if not source_speech:
            continue
        primary_transcript = transcribe_source(source, out_dir, source_index, args.transcribe_model)
        transcripts_by_source[source_index] = primary_transcript
        decisions = build_retake_decisions(
            primary_transcript,
            mode=args.retake_mode,
            confidence_threshold=args.retake_confidence_threshold,
            source_index=source_index,
            transcript_source="primary",
        )
        auxiliary_transcript_paths: list[str] = []
        if args.retake_mode in {"smart", "aggressive"} and args.transcribe_model != "base":
            auxiliary_transcript = transcribe_source(source, out_dir, source_index, "base")
            auxiliary_transcript_paths.append(display_path(transcript_sidecar_path(source, out_dir, source_index, "base")))
            decisions.extend(
                build_retake_decisions(
                    auxiliary_transcript,
                    mode=args.retake_mode,
                    confidence_threshold=args.retake_confidence_threshold,
                    source_index=source_index,
                    transcript_source="auxiliary-base",
                )
            )
        decisions = merge_retake_decisions(decisions)
        plan = plan_retakes_from_decisions(
            source_speech,
            decisions,
            mode=args.retake_mode,
            confidence_threshold=args.retake_confidence_threshold,
            source_index=source_index,
        )
        planned.extend(
            SpeechSegment(
                source_index=timed_range.source_index,
                start=timed_range.start,
                end=timed_range.end,
            )
            for timed_range in plan.speech_segments
        )
        review = plan.to_jsonable()
        review["source"] = display_path(source)
        review["transcript_path"] = display_path(transcript_sidecar_path(source, out_dir, source_index, args.transcribe_model))
        review["auxiliary_transcript_paths"] = auxiliary_transcript_paths
        reviews.append(review)

    boundary_decisions = build_boundary_phrase_restart_decisions(
        [
            phrase_attempts_from_transcript(transcripts_by_source[source_index], source_index=source_index)
            if source_index in transcripts_by_source
            else []
            for source_index in range(len(sources))
        ],
        mode=args.retake_mode,
        threshold=args.retake_confidence_threshold,
        padding_sec=0.12,
        source_durations=[
            float(transcripts_by_source[source_index].duration) if source_index in transcripts_by_source else 0.0
            for source_index in range(len(sources))
        ],
    )
    if boundary_decisions:
        planned_ranges = [
            TimedRange(segment.source_index, segment.start, segment.end)
            for segment in planned
        ]
        boundary_decisions = extend_boundary_orphan_tails(
            boundary_decisions,
            planned_ranges,
            transcripts_by_source,
        )
        planned = [
            SpeechSegment(source_index=timed_range.source_index, start=timed_range.start, end=timed_range.end)
            for timed_range in apply_retake_decisions(planned_ranges, boundary_decisions)
        ]
        reviews.append(
            {
                "mode": args.retake_mode,
                "confidence_threshold": args.retake_confidence_threshold,
                "source_index": None,
                "source": "source_boundary",
                "transcript_path": None,
                "auxiliary_transcript_paths": [],
                "speech_segments": [asdict(segment) for segment in planned],
                "decisions": [
                    {
                        "status": decision.status,
                        "reason": decision.reason,
                        "confidence": decision.confidence,
                        "attempts": [asdict(attempt) for attempt in decision.attempts],
                        "chosen_ranges": [asdict(rng) for rng in decision.chosen_ranges],
                        "removed_ranges": [asdict(rng) for rng in decision.removed_ranges],
                        "splice": decision.splice,
                        "group_type": decision.group_type,
                        "features": decision.features or {},
                        "kept_attempt_indexes": decision.kept_attempt_indexes or [],
                        "removed_attempt_indexes": decision.removed_attempt_indexes or [],
                        "transcript_source": decision.transcript_source,
                    }
                    for decision in boundary_decisions
                ],
                "summary": {
                    "applied": sum(1 for decision in boundary_decisions if decision.status == "applied"),
                    "needs_review": sum(1 for decision in boundary_decisions if decision.status == "needs_review"),
                    "ignored": sum(1 for decision in boundary_decisions if decision.status == "ignored"),
                },
            }
        )

    return sorted(planned, key=lambda segment: (segment.source_index, segment.start, segment.end)), reviews


def write_retake_review(
    *,
    path: Path,
    reviews: list[dict],
    args: argparse.Namespace,
) -> None:
    applied = sum(review.get("summary", {}).get("applied", 0) for review in reviews)
    needs_review = sum(review.get("summary", {}).get("needs_review", 0) for review in reviews)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "production_route": "talking_head_dynamic_clean",
                "retake_mode": args.retake_mode,
                "retake_confidence_threshold": args.retake_confidence_threshold,
                "transcribe_model": args.transcribe_model,
                "summary": {
                    "applied": applied,
                    "needs_review": needs_review,
                    "sources": len(reviews),
                },
                "sources": reviews,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def write_metadata(
    *,
    out_dir: Path,
    sources: list[Path],
    brolls: list[Path],
    speech: list[SpeechSegment],
    chunks: list[EditChunk],
    final: Path,
    subtitle_artifacts: dict | None = None,
    title_artifacts: dict | None = None,
    args: argparse.Namespace,
) -> None:
    edit_notes = [
        "Failed-take removal should be reviewed manually when duplicate takes are present.",
    ]
    if subtitle_artifacts:
        edit_notes.append("Subtitles are burned into final_subtitled.mp4 using the standard subtitle renderer.")
    else:
        edit_notes.append("Subtitles, music, brand overlays, and look effects are intentionally disabled.")
    if title_artifacts:
        edit_notes.append("An animated challenge title is overlaid onto the start, producing final_titled.mp4.")

    (out_dir / "edit_decisions.json").write_text(
        json.dumps(
            {
                "production_route": "talking_head_dynamic_clean",
                "sources": [display_path(path) for path in sources],
                "speech_segments": [asdict(segment) for segment in speech],
                "chunks": chunks_with_output_timing(
                    chunks,
                    transition_duration=args.transition_duration_sec,
                ),
                "notes": edit_notes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "render_metadata.json").write_text(
        json.dumps(
            {
                "production_route": "talking_head_dynamic_clean",
                "final": display_path(final),
                "width": W,
                "height": H,
                "fps": FPS,
                "inputs": [display_path(path) for path in sources],
                "broll_pool": [display_path(path) for path in brolls],
                "silence_threshold_db": args.silence_threshold_db,
                "min_silence_sec": args.min_silence_sec,
                "target_visual_beat_sec": args.target_beat_sec,
                "max_visual_beat_sec": args.max_beat_sec,
                "transition": args.transition,
                "transition_duration_sec": args.transition_duration_sec,
                "retake_mode": args.retake_mode,
                "retake_confidence_threshold": args.retake_confidence_threshold,
                "transcribe_model": args.transcribe_model,
                "subtitles": subtitle_artifacts or {
                    "enabled": False,
                    "style": args.subtitle_style,
                },
                "title_overlay": title_artifacts or {"enabled": False},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a clean dynamic talking-head edit with optional standard-style subtitles."
    )
    parser.add_argument("--slug", required=True, help="Video folder name under output/. Versions live inside it.")
    parser.add_argument(
        "--version",
        help="Render into a specific version subfolder (e.g. v2), overwriting it. "
        "Default: create the next vN so previous renders are kept.",
    )
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        type=Path,
        help="Raw talking-head source video. Repeat for several clips — each is a "
        "separate source whose rotation is handled independently. Required unless "
        "--clips-dir is given.",
    )
    parser.add_argument(
        "--clips-dir",
        type=Path,
        help="Folder of a day's talking-head clips. Every video file inside is "
        "ingested as its own --input (sorted by name), so per-clip rotation is "
        "always handled correctly. Never pre-concatenate mixed-rotation clips.",
    )
    parser.add_argument(
        "--keep-full-source",
        action="append",
        default=[],
        type=int,
        help="Source index (0-based, in input order) to keep IN FULL — no silence "
        "trimming or retake removal. Use for silent demo/screen clips so the demo "
        "plays end-to-end. Repeatable.",
    )
    parser.add_argument("--broll", action="append", default=[], type=Path, help="Optional b-roll video file.")
    parser.add_argument("--broll-dir", type=Path, help="Optional directory with b-roll video files.")
    parser.add_argument("--silence-threshold-db", type=float, default=-40.0)
    parser.add_argument("--min-silence-sec", type=float, default=0.75)
    parser.add_argument("--speech-pad-sec", type=float, default=0.28)
    parser.add_argument("--min-keep-sec", type=float, default=0.65)
    parser.add_argument("--target-beat-sec", type=float, default=3.0)
    parser.add_argument("--max-beat-sec", type=float, default=4.0)
    parser.add_argument("--min-beat-sec", type=float, default=1.2)
    parser.add_argument("--transition", default="micro_push", help="Transition label stored in edit decisions.")
    parser.add_argument("--transition-duration-sec", type=float, default=0.18)
    parser.add_argument("--edit-decisions-json", type=Path, help="Render an explicit chunk plan instead of auto-detecting speech.")
    parser.add_argument("--retake-mode", choices=["off", "safe", "smart", "aggressive"], default="smart")
    parser.add_argument("--retake-confidence-threshold", type=float, default=0.78)
    parser.add_argument("--transcribe-model", default="large-v3")
    parser.add_argument(
        "--retake-review-json",
        type=Path,
        help="Path for retake review decisions. Defaults to retake_decisions.json in the output folder.",
    )
    parser.add_argument(
        "--split-long-speech",
        action="store_true",
        help="Allow plan changes inside long speech segments. Off by default: changes happen only at detected pauses.",
    )
    parser.add_argument(
        "--subtitles",
        action="store_true",
        help="Burn standard-mode subtitles onto an additional final_subtitled.mp4 output.",
    )
    parser.add_argument(
        "--subtitle-style",
        help="Subtitle style from config.yaml. Defaults to edit_profile.subtitle_style, usually editorial_pop.",
    )
    parser.add_argument(
        "--title",
        type=Path,
        help="Animated challenge-title clip (text on a black background, 1080x1920, "
        "pre-positioned top-left). Overlaid onto the start of the video, producing "
        "an additional final_titled.mp4. Keying/fade defaults: config.yaml title_overlay.",
    )
    parser.add_argument(
        "--title-fade-out-sec",
        type=float,
        default=None,
        help="Override the title fade-out duration (default: title_overlay.fade_out_sec).",
    )
    parser.add_argument(
        "--auto-title",
        action="store_true",
        help="Overlay the default challenge title (config.yaml title_overlay.default_title) "
        "without naming it. For challenge-day videos so the title is added automatically. "
        "Ignored if --title is given explicitly.",
    )
    return parser.parse_args()


def resolve_title_clip(args: argparse.Namespace, cfg: object) -> Path | None:
    """Decide which title clip to overlay: explicit --title, else --auto-title default."""
    if args.title is not None:
        return args.title.resolve() if args.title.is_absolute() else (ROOT / args.title).resolve()
    if getattr(args, "auto_title", False):
        default_title = getattr(cfg.title_overlay, "default_title", None)
        if not default_title:
            print(
                "     warning: --auto-title set but config.yaml title_overlay.default_title is empty — "
                "no title overlaid.",
                flush=True,
            )
            return None
        path = Path(default_title)
        resolved = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
        print(f"     auto-title: overlaying challenge title {display_path(resolved)}", flush=True)
        return resolved
    return None


def resolve_input_paths(args: argparse.Namespace) -> list[Path]:
    """Collect source clip paths from --input and/or --clips-dir."""
    paths = [path.resolve() if path.is_absolute() else (ROOT / path).resolve() for path in args.input]
    if args.clips_dir:
        clips_dir = args.clips_dir.resolve() if args.clips_dir.is_absolute() else (ROOT / args.clips_dir).resolve()
        if not clips_dir.is_dir():
            raise NotADirectoryError(clips_dir)
        paths.extend(
            path
            for path in sorted(clips_dir.iterdir())
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        )
    if not paths:
        raise SystemExit("No input clips: pass --input <file> (repeatable) or --clips-dir <folder>.")
    return paths


def main() -> None:
    args = parse_args()
    sources = organize_talking_head_sources(
        resolve_input_paths(args),
        slug=args.slug,
    )
    for source in sources:
        if not source.exists():
            raise FileNotFoundError(source)
    log_source_orientation(sources)

    keep_full = {index for index in args.keep_full_source if 0 <= index < len(sources)}
    for index in args.keep_full_source:
        if index not in keep_full:
            print(f"     warning: --keep-full-source {index} is out of range (have {len(sources)} sources)", flush=True)

    broll_dir = None
    if args.broll_dir:
        broll_dir = args.broll_dir.resolve() if args.broll_dir.is_absolute() else (ROOT / args.broll_dir).resolve()
    brolls = collect_broll(
        [path.resolve() if path.is_absolute() else (ROOT / path).resolve() for path in args.broll],
        broll_dir,
    )

    base_dir = OUTPUT_ROOT / args.slug
    previous_dir = latest_version_dir(base_dir)
    out_dir = versioned_dir(base_dir, version=args.version)
    seed_reusable(previous_dir, out_dir, ["*.transcript.json*"])
    review_path = retake_review_path(args, out_dir)

    speech: list[SpeechSegment] = []
    retake_reviews: list[dict] = []
    if args.edit_decisions_json:
        decisions_path = (
            args.edit_decisions_json.resolve()
            if args.edit_decisions_json.is_absolute()
            else (ROOT / args.edit_decisions_json).resolve()
        )
        chunks = load_decision_chunks(decisions_path)
        speech = [
            SpeechSegment(
                source_index=chunk.source_index,
                start=chunk.start,
                end=chunk.end,
            )
            for chunk in chunks
        ]
        retake_reviews = [
            {
                "mode": "off",
                "reason": "explicit edit decisions provided; retake planning skipped",
                "decisions": [],
                "summary": {"applied": 0, "needs_review": 0, "ignored": 0},
            }
        ]
    else:
        detected: list[SpeechSegment] = []
        full_segments: list[SpeechSegment] = []
        for source_index, source in enumerate(sources):
            duration = ffprobe_duration(source)
            if source_index in keep_full:
                full_segments.append(SpeechSegment(source_index, 0.0, duration))
                print(f"     keep-full: [{source_index}] {source.name} kept in full ({duration:.1f}s, no trim)", flush=True)
                continue
            silences = detect_silences(source, args.silence_threshold_db, args.min_silence_sec)
            detected.extend(
                speech_from_silences(
                    source_index=source_index,
                    duration=duration,
                    silences=silences,
                    pad=args.speech_pad_sec,
                    min_keep=args.min_keep_sec,
                )
            )

        if not detected and not full_segments:
            raise RuntimeError("No speech segments detected. Try lowering --silence-threshold-db or --min-silence-sec.")

        if detected:
            planned, retake_reviews = plan_speech_with_retakes(
                sources=sources,
                speech=detected,
                out_dir=out_dir,
                args=args,
            )
            warn_on_large_retake_drops(detected, planned, sources)
        else:
            planned, retake_reviews = [], []

        speech = sorted(planned + full_segments, key=lambda seg: (seg.source_index, seg.start))
        if not speech:
            raise RuntimeError("Retake planning removed every speech segment. Try --retake-mode off.")

        chunks = make_chunks(
            sources,
            speech,
            brolls,
            args.target_beat_sec,
            args.max_beat_sec,
            args.min_beat_sec,
            args.transition,
            args.split_long_speech,
        )
        if keep_full:
            # Demo/screen clips kept in full must not be cropped ("close") — force
            # an un-cropped medium beat so the whole screen stays visible.
            chunks = [
                replace(chunk, plan="medium", broll=None) if chunk.source_index in keep_full else chunk
                for chunk in chunks
            ]
    write_retake_review(path=review_path, reviews=retake_reviews, args=args)
    quality_transcripts = load_or_transcribe_quality_transcripts(
        sources,
        out_dir,
        args.transcribe_model,
        transcribe_missing=args.subtitles,
    )
    quality_report = build_timeline_quality_report(
        chunks=chunks,
        transcripts_by_source=quality_transcripts,
        transition_duration=args.transition_duration_sec,
    )
    write_quality_report(out_dir / "quality_report.json", quality_report)
    clean_final = render(sources, chunks, out_dir, args.transition_duration_sec)
    final = clean_final
    subtitle_artifacts = None
    if args.subtitles:
        subtitle_artifacts = render_talking_head_subtitles(
            clean_video=clean_final,
            chunks=chunks,
            transcripts_by_source=quality_transcripts,
            out_dir=out_dir,
            args=args,
        )
        final = out_dir / "final_subtitled.mp4"

    title_artifacts = None
    title_clip = resolve_title_clip(args, _load_render_config())
    if title_clip:
        if not title_clip.exists():
            raise FileNotFoundError(title_clip)
        titled = burn_title_overlay(
            final,
            title_clip,
            out_dir / "final_titled.mp4",
            _load_render_config(),
            fade_out_sec=args.title_fade_out_sec,
        )
        title_artifacts = {
            "enabled": True,
            "clip": display_path(title_clip),
            "final": display_path(titled),
        }
        final = titled

    write_metadata(
        out_dir=out_dir,
        sources=sources,
        brolls=brolls,
        speech=speech,
        chunks=chunks,
        final=final,
        subtitle_artifacts=subtitle_artifacts,
        title_artifacts=title_artifacts,
        args=args,
    )
    print(final)


if __name__ == "__main__":
    main()
