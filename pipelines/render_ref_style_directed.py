from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.output_paths import versioned_dir
from src.ref_style_director import RefStyleEditPlan, build_director_prompt, build_edit_plan
from src.schemas import Transcript, Word

W = 1080
H = 1920
FPS = 30
HOOK_ZOOM_DELTA = 0.28
HOOK_ZOOM_OUT_SEC = 1.0

# Close-up push-in (~1.28x) used to alternate framing across talking-head beats.
CLOSE_CROP_W = 842
CLOSE_CROP_H = 1498
CLOSE_CROP_X = (W - CLOSE_CROP_W) // 2
CLOSE_CROP_Y = round((H - CLOSE_CROP_H) * 0.32)

DISPLAY_FIXES = {
    "совреть": "своё",
    "проверя.": "проверяй.",
    "провера.": "проверяй.",
    "провера": "проверяй",
}

# Phrases that get the big-face triple-stamp treatment (close-up, no blue cover,
# the words repeated across three lines like the reference grunge stamp).
EMPHASIS_PHRASES: list[list[str]] = [["умный", "отклик"]]


def rounded_alpha(width: int, height: int, radius: int) -> str:
    """geq filter that carves rounded corners into an rgba image via its alpha."""
    xr = width - 1 - radius
    yr = height - 1 - radius
    r2 = radius * radius
    dx = f"max(0\\,max({radius}-X\\,X-{xr}))"
    dy = f"max(0\\,max({radius}-Y\\,Y-{yr}))"
    return (
        "geq=r='r(X\\,Y)':g='g(X\\,Y)':b='b(X\\,Y)':"
        f"a='255*lte(pow({dx}\\,2)+pow({dy}\\,2)\\,{r2})'"
    )


def run(cmd: list[str], label: str) -> None:
    print(f"-> {label}", flush=True)
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def probe_color_transfer(path: Path) -> str:
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-select_streams", "v:0",
            "-show_entries", "stream=color_transfer", "-of", "csv=p=0", str(path),
        ],
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def build_tech_chain(source: Path) -> str:
    """Colour-normalise the live talk layer to BT.709 SDR.

    Only genuinely HDR footage (iPhone HLG/PQ) is tonemapped. An already-SDR
    source (e.g. talking_head.mp4) is passed through neutrally — the previous code
    tonemapped unconditionally plus a heavy eq boost, which washed out / mis-graded
    SDR talking-head footage. The neutral path keeps the natural reference look
    (ref.mp4): no brightness/gamma push, only a whisper of contrast/saturation on
    real HDR conversions where the gamut squeeze needs compensating.
    """
    transfer = probe_color_transfer(source)
    is_hdr = transfer in ("arib-std-b67", "smpte2084")  # HLG / PQ
    if is_hdr:
        return (
            f"fps={FPS},"
            "zscale=t=linear:npl=1000,format=gbrpf32le,"
            "zscale=p=bt709,tonemap=tonemap=mobius:desat=0,"
            "zscale=t=bt709:m=bt709:r=limited,format=yuv420p,"
            "eq=saturation=1.04:contrast=1.02"
        )
    return (
        f"fps={FPS},"
        "scale=iw:ih:in_range=auto:out_range=tv:"
        "in_color_matrix=auto:out_color_matrix=bt709,"
        "format=yuv420p"
    )


def q(value: str) -> str:
    return value.replace("'", "\\'")


def ass_time(value: float) -> str:
    cs = max(0, round(value * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def dialogue(layer: int, start: float, end: float, style: str, text: str, tags: str) -> str:
    if end <= start:
        end = start + 0.34
    return f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{tags}{text}"


def marker_underline(start: float, end: float, x: float, y: float, *, scale: int = 100) -> str:
    """Hand-drawn red marker underline stroke (ASS vector ribbon) as an accent."""
    tags = (
        f"{{\\an5\\pos({x:.0f},{y:.0f})\\1c&H2028E0&\\bord0\\shad0"
        f"\\fscx{scale}\\fscy{scale}\\frz-2.5\\fad(120,90)"
        "\\t(0,150,\\fscx" + str(scale + 6) + "\\fscy" + str(scale + 6) + ")\\p1}"
    )
    path = (
        "m -212 4 b -110 -14 -10 12 96 -8 b 150 -16 196 -2 212 -10 l 208 12 "
        "b 150 24 96 6 0 18 b -96 28 -160 8 -210 22"
    )
    return dialogue(2, start, end, "Marker", path + "{\\p0}", tags)


def clean_text(text: str, *, uppercase: bool = True) -> str:
    value = re.sub(r"\s+", " ", text.strip())
    value = value.strip(" ,.!?;:…")
    if uppercase:
        value = value.upper()
    return value


def load_transcript(source: Path, transcript_path: Path) -> Transcript:
    if transcript_path.exists():
        return Transcript.model_validate_json(transcript_path.read_text(encoding="utf-8"))

    from src.transcribe import transcribe

    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    return transcribe(source, transcript_path, language="ru", model_size="large-v3")


def _clean_slug(clean_dir: Path) -> str:
    output_root = ROOT / "output"
    try:
        return str(clean_dir.resolve().relative_to(output_root.resolve()))
    except ValueError:
        return str(clean_dir.name)


def _clean_input_path(source: Path, clean_dir: Path) -> Path:
    if source.resolve().parent != ROOT.resolve():
        return source
    clean_dir.mkdir(parents=True, exist_ok=True)
    copied = clean_dir / f"source{source.suffix}"
    if not copied.exists() or copied.stat().st_size != source.stat().st_size:
        shutil.copy2(source, copied)
    return copied


def normalize_transcript_word_durations(transcript: Transcript) -> Transcript:
    words: list[Word] = []
    for word in transcript.words:
        end = word.end
        if end - word.start < 0.04:
            end = min(transcript.duration, word.start + 0.18)
        words.append(Word(word=word.word, start=word.start, end=end))
    return Transcript(words=words, full_text=transcript.full_text, duration=transcript.duration)


def _word_key(text: str) -> str:
    return re.sub(r"[^а-яёa-z0-9]+", "", text.lower().replace("ё", "е"))


def find_phrase_windows(
    transcript: Transcript, phrases: list[list[str]] = EMPHASIS_PHRASES
) -> list[tuple[float, float]]:
    keyed = [
        (_word_key(word.word), float(word.start), float(word.end))
        for word in transcript.words
        if _word_key(word.word)
    ]
    windows: list[tuple[float, float]] = []
    for phrase in phrases:
        target = [_word_key(part) for part in phrase]
        span = len(target)
        for i in range(len(keyed) - span + 1):
            if [keyed[i + j][0] for j in range(span)] == target:
                windows.append((keyed[i][1], keyed[i + span - 1][2]))
    return windows


def restore_missing_terminal_words(source_transcript: Transcript, clean_transcript: Transcript) -> Transcript:
    clean_keys = [_word_key(word.word) for word in clean_transcript.words if _word_key(word.word)]
    missing: list[Word] = []
    for word in source_transcript.words[-4:]:
        key = _word_key(word.word)
        if key and key not in clean_keys[-5:]:
            missing.append(word)

    if not missing:
        return clean_transcript

    words = list(clean_transcript.words)
    cursor = max(0.0, clean_transcript.duration - 0.20 * len(missing))
    for word in missing:
        end = min(clean_transcript.duration, cursor + 0.18)
        if end <= cursor:
            end = cursor + 0.18
        words.append(Word(word=word.word, start=round(cursor, 3), end=round(end, 3)))
        cursor = end

    duration = round(max(clean_transcript.duration, words[-1].end), 3)
    return Transcript(
        words=words,
        full_text=" ".join(word.word.strip() for word in words if word.word.strip()),
        duration=duration,
    )


def pause_tight_chunks(
    transcript: Transcript,
    *,
    max_gap_sec: float = 0.45,
    retain_gap_sec: float | None = None,
    edge_pad_sec: float = 0.14,
    outgoing_pad_sec: float = 0.46,
    incoming_pad_sec: float | None = 0.32,
    tail_pad_sec: float | None = None,
) -> list[dict]:
    """Build source ranges that remove dead air while preserving word edges."""
    words = [word for word in normalize_transcript_word_durations(transcript).words if word.word.strip()]
    if not words:
        return [{"start": 0.0, "end": round(float(transcript.duration), 3)}]

    if retain_gap_sec is not None:
        outgoing_pad = retain_gap_sec / 2
        incoming_pad = retain_gap_sec / 2
    else:
        outgoing_pad = outgoing_pad_sec
        incoming_pad = edge_pad_sec if incoming_pad_sec is None else incoming_pad_sec
    tail_pad = edge_pad_sec * 3 if tail_pad_sec is None else tail_pad_sec
    chunks: list[dict] = []
    start = max(0.0, float(words[0].start) - incoming_pad)
    previous = words[0]
    for word in words[1:]:
        gap = float(word.start) - float(previous.end)
        if gap > max_gap_sec:
            end = min(float(transcript.duration), float(previous.end) + outgoing_pad)
            if end > start:
                chunks.append({"start": round(start, 3), "end": round(end, 3)})
            start = max(0.0, float(word.start) - incoming_pad)
        previous = word

    end = min(float(transcript.duration), float(words[-1].end) + tail_pad)
    if end > start:
        chunks.append({"start": round(start, 3), "end": round(end, 3)})

    merged: list[dict] = []
    for chunk in chunks:
        if not merged or float(chunk["start"]) > float(merged[-1]["end"]) + 0.04:
            merged.append(chunk)
            continue
        merged[-1]["end"] = round(max(float(merged[-1]["end"]), float(chunk["end"])), 3)
    return merged


def tighten_transcript_to_chunks(transcript: Transcript, chunks: list[dict]) -> Transcript:
    words: list[Word] = []
    out_cursor = 0.0
    for chunk in chunks:
        chunk_start = float(chunk["start"])
        chunk_end = float(chunk["end"])
        for word in transcript.words:
            midpoint = (float(word.start) + float(word.end)) / 2
            if not (chunk_start <= midpoint <= chunk_end):
                continue
            start = out_cursor + max(0.0, float(word.start) - chunk_start)
            end = out_cursor + max(0.0, float(word.end) - chunk_start)
            if end <= start:
                end = start + 0.06
            words.append(Word(word=word.word, start=round(start, 3), end=round(end, 3)))
        out_cursor += max(0.0, chunk_end - chunk_start)

    duration = round(out_cursor, 3)
    return Transcript(
        words=words,
        full_text=" ".join(word.word.strip() for word in words if word.word.strip()),
        duration=duration,
    )


def render_pause_tight_video(source: Path, out_path: Path, chunks: list[dict]) -> None:
    if len(chunks) == 1 and abs(float(chunks[0]["start"])) < 0.001:
        shutil.copy2(source, out_path)
        return

    filter_parts: list[str] = []
    concat_inputs: list[str] = []
    for index, chunk in enumerate(chunks):
        start = float(chunk["start"])
        end = float(chunk["end"])
        filter_parts.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{index}]"
        )
        filter_parts.append(
            f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{index}]"
        )
        concat_inputs.append(f"[v{index}][a{index}]")
    filter_parts.append(
        "".join(concat_inputs) + f"concat=n={len(chunks)}:v=1:a=1[vout][aout]"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-r",
            str(FPS),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(out_path),
        ],
        "render pause-tight talking-head prelayer",
    )


def tighten_pause_prelayer(
    source: Path,
    transcript: Transcript,
    *,
    out_dir: Path,
    force: bool = False,
) -> tuple[Path, Transcript, Path, list[dict]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    tight_video = out_dir / "final_tight.mp4"
    tight_transcript_path = out_dir / "transcript_tight.json"
    chunks_path = out_dir / "pause_tight_chunks.json"

    if not force and tight_video.exists() and tight_transcript_path.exists() and chunks_path.exists():
        return (
            tight_video,
            Transcript.model_validate_json(tight_transcript_path.read_text(encoding="utf-8")),
            tight_transcript_path,
            json.loads(chunks_path.read_text(encoding="utf-8")),
        )

    chunks = pause_tight_chunks(transcript)
    tight_transcript = tighten_transcript_to_chunks(transcript, chunks)
    render_pause_tight_video(source, tight_video, chunks)
    tight_transcript_path.write_text(tight_transcript.model_dump_json(indent=2), encoding="utf-8")
    chunks_path.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    return tight_video, tight_transcript, tight_transcript_path, chunks


def transcript_protected_chunks(
    chunks: list[dict],
    transcript: Transcript,
    *,
    source: str,
    word_pad_sec: float = 0.12,
    merge_gap_sec: float = 0.35,
) -> list[dict]:
    ranges: list[tuple[float, float]] = []
    for chunk in chunks:
        start = float(chunk["start"])
        end = float(chunk["end"])
        if end > start:
            ranges.append((start, end))

    for word in normalize_transcript_word_durations(transcript).words:
        if not word.word.strip():
            continue
        start = max(0.0, word.start - word_pad_sec)
        end = min(transcript.duration, word.end + word_pad_sec)
        if end > start:
            ranges.append((start, end))

    merged: list[tuple[float, float]] = []
    for start, end in sorted(ranges):
        if not merged or start - merged[-1][1] > merge_gap_sec:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))

    protected: list[dict] = []
    for index, (start, end) in enumerate(merged):
        if end - start < 0.08:
            continue
        protected.append(
            {
                "index": len(protected),
                "source_index": 0,
                "source": source,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
                "plan": "medium",
                "transition": "micro_push",
                "broll": None,
            }
        )
    return protected


def _write_protected_edit_decisions(path: Path, chunks: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "production_route": "talking_head_dynamic_clean",
                "chunks": chunks,
                "notes": ["Transcript-protected clean cut: audio silence cuts expanded to preserve words."],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def prepare_clean_prelayer(
    source: Path,
    *,
    clean_dir: Path,
    source_transcript_path: Path | None = None,
    force: bool = False,
) -> tuple[Path, Transcript, Path]:
    clean_dir.mkdir(parents=True, exist_ok=True)
    clean_video = clean_dir / "final_clean.mp4"
    transcript_path = clean_dir / "transcript.json"

    if not force and clean_video.exists() and transcript_path.exists():
        return clean_video, load_transcript(clean_video, transcript_path), transcript_path

    clean_input = _clean_input_path(source, clean_dir)
    run(
        [
            sys.executable,
            str(ROOT / "pipelines/render_talking_head_dynamic_clean.py"),
            "--slug",
            _clean_slug(clean_dir),
            "--input",
            str(clean_input),
            "--retake-mode",
            "off",
            "--silence-threshold-db",
            "-30",
            "--min-silence-sec",
            "0.35",
            "--speech-pad-sec",
            "0.18",
            "--min-keep-sec",
            "0.10",
            "--target-beat-sec",
            "30",
            "--max-beat-sec",
            "30",
            "--min-beat-sec",
            "0.8",
            "--transition-duration-sec",
            "0",
        ],
        "render clean talking-head prelayer",
    )
    from pipelines.render_talking_head_dynamic_clean import (
        build_talking_head_timeline_transcript,
        load_decision_chunks,
    )

    source_transcript = normalize_transcript_word_durations(
        load_transcript(
            source,
            source_transcript_path or clean_dir / "source.transcript.json",
        )
    )
    raw_decisions = json.loads((clean_dir / "edit_decisions.json").read_text(encoding="utf-8"))
    raw_chunks = raw_decisions["chunks"] if isinstance(raw_decisions, dict) else raw_decisions
    protected_decisions_path = clean_dir / "protected_edit_decisions.json"
    _write_protected_edit_decisions(
        protected_decisions_path,
        transcript_protected_chunks(raw_chunks, source_transcript, source=str(clean_input)),
    )
    run(
        [
            sys.executable,
            str(ROOT / "pipelines/render_talking_head_dynamic_clean.py"),
            "--slug",
            _clean_slug(clean_dir),
            "--input",
            str(clean_input),
            "--edit-decisions-json",
            str(protected_decisions_path),
            "--retake-mode",
            "off",
            "--transition-duration-sec",
            "0",
        ],
        "render transcript-protected clean prelayer",
    )

    chunks = load_decision_chunks(clean_dir / "edit_decisions.json")
    clean_transcript = build_talking_head_timeline_transcript(
        chunks,
        {0: source_transcript},
        transition_duration=0.0,
    )
    clean_transcript = restore_missing_terminal_words(source_transcript, clean_transcript)
    transcript_path.write_text(clean_transcript.model_dump_json(indent=2), encoding="utf-8")
    return clean_video, clean_transcript, transcript_path


def load_words(transcript: Transcript) -> list[dict]:
    words: list[dict] = []
    for raw in transcript.words:
        text = raw.word.strip()
        if not text:
            continue
        text = DISPLAY_FIXES.get(text.lower(), text)
        start = float(raw.start)
        end = float(raw.end)
        if end <= start:
            end = start + 0.34
        # Glue hyphen suffixes ("26" + "-м" -> "26-м") onto the previous word so
        # number/ordinal endings stay on one token instead of flashing apart.
        if text.startswith("-") and words:
            words[-1]["word"] = words[-1]["word"] + text
            words[-1]["end"] = max(words[-1]["end"], end)
            continue
        words.append({"word": text, "start": start, "end": end})
    return words


def format_for_time(plan: RefStyleEditPlan, timestamp: float) -> str:
    if not plan.segments:
        return "format_1_hook_metal"
    if timestamp < plan.segments[0].start:
        return plan.segments[0].format_id
    previous = plan.segments[0].format_id
    for segment in plan.segments:
        if segment.start <= timestamp < segment.end:
            return segment.format_id
        if timestamp >= segment.end:
            previous = segment.format_id
    return previous


def chunk_words(words: list[dict], plan: RefStyleEditPlan) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    current: list[dict] = []

    def max_words_for(format_id: str) -> int:
        return {
            "format_1_hook_metal": 1,
            "format_2_framed_face": 3,
            "format_3_turn_badge": 2,
            "format_4_blue_demo": 2,
            "format_5_lower_demo_cta": 2,
        }.get(format_id, 2)

    def segment_index_for(t: float) -> int:
        for i, segment in enumerate(plan.segments):
            if segment.start <= t < segment.end:
                return i
        return -1

    for word in words:
        format_id = format_for_time(plan, word["start"])
        if not current:
            current = [word]
            continue
        previous = current[-1]
        current_format = format_for_time(plan, current[0]["start"])
        same_format = format_id == current_format
        # Never let one caption span two beats: a beat boundary always ends the
        # caption. (Keeps e.g. "...ATS" on the analysis beat and starts "потом ..."
        # fresh on the next beat instead of gluing them across the pause.)
        same_segment = segment_index_for(word["start"]) == segment_index_for(current[0]["start"])
        gap = word["start"] - previous["end"]
        if not same_format or not same_segment or gap > 0.38 or len(current) >= max_words_for(current_format):
            chunks.append(current)
            current = [word]
        else:
            current.append(word)
    if current:
        chunks.append(current)
    return chunks


def _metal_caption_tags(size: int, scale_x: int, y: int) -> tuple[str, str]:
    """Ref-style sticker lettering for the hook and CTA.

    A bold opaque white word with a thick dark edge sits over a saturated red
    echo offset down-right, both popping in together. Replaces the previous
    bleached near-white layer that had almost no contrast on the talking head.
    """
    echo = (
        "{\\an5\\fad(50,60)"
        f"\\pos(552,{y + 12})\\fs{size}\\fscx{scale_x + 2}\\fscy106"
        "\\blur0.5\\alpha&H16&"
        f"\\t(0,150,\\fscx{scale_x - 2}\\fscy100\\blur0.4)"
        "}"
    )
    main = (
        "{\\an5\\fad(50,60)"
        f"\\pos(540,{y})\\fs{size}\\fscx{scale_x + 8}\\fscy114"
        "\\blur0.10\\alpha&H00&"
        f"\\t(0,150,\\fscx{scale_x}\\fscy100)"
        "}"
    )
    return echo, main


def write_ass(
    path: Path,
    transcript: Transcript,
    plan: RefStyleEditPlan,
    emphasis_phrases: list[list[str]] = EMPHASIS_PHRASES,
) -> None:
    words = load_words(transcript)
    chunks = chunk_words(words, plan)
    header = """[Script Info]
Title: Directed Ref Style Subtitles
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Metal,Bebas Neue Cyrillic,132,&H00F2F7FF,&H000000FF,&H00121317,&H00000000,-1,0,0,0,100,100,0,0,1,6,0,5,20,20,20,1
Style: MetalCyan,Bebas Neue Cyrillic,132,&H002030E8,&H000000FF,&H00121317,&H00000000,-1,0,0,0,100,100,0,0,1,4,0,5,20,20,20,1
Style: ThinPop,Pastry Chef Cyrillic Script,82,&H00FFFFFF,&H000000FF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,20,20,20,1
Style: Badge,Bebas Neue Cyrillic,80,&H00FFFFFF,&H000000FF,&H00652D8B,&H00652D8B,-1,0,0,0,100,100,0,0,3,8,0,5,20,20,20,1
Style: RetroDark,Ruslan Display,138,&H00EDE0A5,&H000000FF,&H00353C31,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,20,20,20,1
Style: RetroCream,Ruslan Display,132,&H006D3F85,&H000000FF,&H00EEE2A5,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,20,20,20,1
Style: PosterWhite,Bebas Neue Cyrillic,104,&H00FFFFFF,&H000000FF,&H00202A3A,&H70000000,-1,0,0,0,100,100,0,0,1,5,2,5,20,20,20,1
Style: EditorialWhite,Onest,92,&H00FFFFFF,&H000000FF,&H00000000,&H70000000,-1,0,0,0,100,100,0,0,1,4,2,5,20,20,20,1
Style: EditorialYellow,Bebas Neue Cyrillic,128,&H0000F5FF,&H000000FF,&H00000000,&H50000000,-1,0,0,0,100,100,0,0,1,4,4,5,20,20,20,1
Style: PlainWhite,Onest,86,&H00FFFFFF,&H000000FF,&H30505050,&H90000000,-1,0,0,0,100,100,0,0,1,2,2,5,20,20,20,1
Style: Marker,Arial,40,&H002028E0,&H000000FF,&H002028E0,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,20,20,20,1
Style: Stamp,Bebas Neue Cyrillic,150,&H30FFFFFF,&H000000FF,&H60202A3A,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,5,20,20,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    format_counts: dict[str, int] = {format_id: 0 for format_id in {
        "format_1_hook_metal",
        "format_2_framed_face",
        "format_3_turn_badge",
        "format_4_blue_demo",
        "format_5_lower_demo_cta",
    }}

    for chunk_index, chunk in enumerate(chunks):
        start = chunk[0]["start"]
        end = chunk[-1]["end"] + 0.22
        # A caption must never outlive the next caption's entrance: two words must
        # never share the screen (most visible in the fast single-word hook, where a
        # min-duration floor used to overrun the next word).
        next_start = chunks[chunk_index + 1][0]["start"] if chunk_index + 1 < len(chunks) else None
        if next_start is not None and next_start > start:
            hard_cap = next_start - 0.04
            if hard_cap <= start:
                hard_cap = (start + next_start) / 2
            end = min(end, hard_cap)
            end = max(end, min(start + 0.30, hard_cap))
        else:
            end = max(end, start + 0.30)
        format_id = format_for_time(plan, start)
        format_counts[format_id] = format_counts.get(format_id, 0) + 1
        index = format_counts[format_id] - 1
        raw_text = " ".join(word["word"] for word in chunk)

        if format_id == "format_1_hook_metal":
            text = clean_text(raw_text, uppercase=True)
            size = max(82, min(154, 174 - len(text) * 4))
            scale_x = max(55, min(90, 94 - len(text) * 2))
            # Kinetic vertical bounce like the reference hook stickers, sitting in
            # the lower third (closer to the bottom, not centered on the face).
            y = 1300 + (index % 3 - 1) * 64
            echo, main = _metal_caption_tags(size, scale_x, y)
            events.append(dialogue(3, start, end, "MetalCyan", text, echo))
            events.append(dialogue(4, start, end, "Metal", text, main))
        elif format_id == "format_2_framed_face":
            text = clean_text(raw_text, uppercase=False)
            # Always above the photo card like the reference yellow scene.
            y = 252
            size = max(88, min(138, 144 - len(text) * 3))
            tags = (
                "{\\an5\\fad(70,80)"
                f"\\pos(540,{y})\\fs{size}\\fscx74\\fscy74\\blur0.12\\alpha&H00&\\bord1.2\\3c&HFFFFFF&"
                "\\t(0,95,\\fscx108\\fscy108\\blur0.08)"
                "\\t(95,180,\\fscx100\\fscy100)"
                "}"
            )
            events.append(dialogue(5, start, end, "ThinPop", text, tags))
        elif format_id == "format_3_turn_badge":
            # Two words stacked in a fixed place: top word + bottom word appear and
            # disappear together. Top and bottom always use the same distinct styles.
            stacked = [clean_text(word["word"], uppercase=True) for word in chunk]
            stacked = [word for word in stacked if word]
            top_text = stacked[0] if stacked else ""
            bottom_text = stacked[1] if len(stacked) > 1 else ""
            if top_text:
                top_tags = (
                    "{\\an5\\pos(540,300)\\fs96\\frz-2\\fscx84\\fscy84"
                    "\\t(0,100,\\fscx108\\fscy108)\\t(100,180,\\fscx100\\fscy100)}"
                )
                events.append(dialogue(6, start, end, "Badge", top_text, top_tags))
            if bottom_text:
                bottom_tags = (
                    "{\\an5\\pos(540,392)\\fs130\\fscx90\\fscy82\\frz1.4\\alpha&H14&"
                    "\\t(0,120,\\alpha&H00&\\fscy100)}"
                )
                events.append(dialogue(7, start, end, "RetroCream", bottom_text, bottom_tags))
        elif format_id == "format_4_blue_demo":
            chunk_keys = [_word_key(word["word"]) for word in chunk]
            is_emphasis = any(
                chunk_keys == [_word_key(part) for part in phrase]
                for phrase in emphasis_phrases
            )
            if is_emphasis:
                # Big-face grunge stamp: the phrase repeated across three lines,
                # semi-transparent, slightly rotated (reference look).
                stamp_text = clean_text(raw_text, uppercase=True)
                for line_y, rot, sx in ((520, -3, 86), (940, 2, 90), (1360, -2, 86)):
                    stamp_tags = (
                        "{\\an5\\fad(70,90)"
                        f"\\pos(540,{line_y})\\fs150\\fscx{sx}\\fscy{sx}\\frz{rot:g}"
                        "\\blur1.1\\alpha&H2E&"
                        f"\\t(0,150,\\fscx{sx + 5}\\fscy{sx + 5}\\alpha&H20&)"
                        "}"
                    )
                    events.append(dialogue(8, start, end, "Stamp", stamp_text, stamp_tags))
                continue
            text = clean_text(raw_text, uppercase=True)
            # Captions always live in a fixed band ABOVE the demo card (the card now
            # starts at y=360), 1-2 words at a time that fade in and out before the
            # next pops — so they never sit on top of the product UI.
            style = "EditorialYellow" if index % 2 else "EditorialWhite"
            size = max(96, min(140, 150 - len(text) * 3))
            # Sticker-style placement: shift the caption left/right and tilt it per
            # beat so it roams like the reference captions instead of sitting in a
            # dead centered band. Stays well ABOVE the demo card (card top y=360).
            anchor_x = 540 + (index % 3 - 1) * 58
            y = 200 + (index % 2) * 20
            tilt = (-2.6, 1.8, -1.4, 2.2)[index % 4]
            tags = (
                "{\\an5\\fad(70,80)"
                f"\\pos({anchor_x},{y})\\fs{size}\\fscx90\\fscy82\\frz{tilt:g}"
                "\\blur0.22\\t(0,110,\\fscx100\\fscy90\\blur0.05)"
                "\\t(110,220,\\fscx94\\fscy86)"
                "}"
            )
            events.append(dialogue(8, start, end, style, text, tags))
        else:
            # CTA closes with the same metallic hook lettering as the opening scene,
            # pinned to the top of the frame so it never covers the product demo.
            # Fixed position (no alternating y) + no underline = no flicker.
            text = clean_text(raw_text, uppercase=True)
            size = max(82, min(150, 168 - len(text) * 4))
            scale_x = max(55, min(90, 94 - len(text) * 2))
            y = 300
            echo, main = _metal_caption_tags(size, scale_x, y)
            events.append(dialogue(3, start, end, "MetalCyan", text, echo))
            events.append(dialogue(4, start, end, "Metal", text, main))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def shot_close_enable(plan: RefStyleEditPlan) -> str:
    """Alternate framing across plain talking-head beats: medium, close, medium...

    Graphics formats (yellow frame / blue demo) already change the picture, so they
    keep the full-frame talk layer. Among the remaining talking-head-only beats we
    start medium (the hook) and flip to a close-up push-in on every other beat.
    """
    plain_formats = {
        "format_1_hook_metal",
        "format_3_turn_badge",
        "format_5_lower_demo_cta",
    }
    ranges: list[str] = []
    plain_index = -1
    for segment in plan.segments:
        if segment.format_id not in plain_formats:
            continue
        plain_index += 1
        if plain_index % 2 == 1:
            ranges.append(f"between(t,{segment.start:.2f},{segment.end:.2f})")
    return "+".join(ranges) if ranges else "0"


def enable_expr(plan: RefStyleEditPlan, format_id: str) -> str:
    ranges = []
    for index, segment in enumerate(plan.segments):
        if segment.format_id != format_id:
            continue
        end = segment.end
        if index + 1 < len(plan.segments):
            end = max(end, plan.segments[index + 1].start)
        else:
            end = max(end, plan.duration)
        ranges.append(f"between(t,{segment.start:.2f},{end:.2f})")
    return "+".join(ranges) if ranges else "0"


def demo_head_cutaways(
    plan: RefStyleEditPlan,
    *,
    first_offset_sec: float = 1.9,
    head_sec: float = 1.4,
    demo_run_sec: float = 2.8,
    min_segment_sec: float = 3.2,
    edge_guard_sec: float = 0.6,
) -> list[tuple[float, float]]:
    """Short talking-head cutaways punched into long blue-demo beats.

    The demo stays the primary visual, but every ~3-4s the blue cover + product
    card are momentarily dropped to reveal the live talking head, adding shot
    changes and trimming the total demo screen-time. Short demo beats are left
    intact, and cutaways never land within ``edge_guard_sec`` of a beat edge so
    they don't collide with the scene-change whip/flash.
    """
    windows: list[tuple[float, float]] = []
    for segment in plan.segments:
        if segment.format_id != "format_4_blue_demo":
            continue
        if segment.end - segment.start < min_segment_sec:
            continue
        cursor = segment.start + first_offset_sec
        limit = segment.end - edge_guard_sec
        while cursor + 0.4 <= limit:
            window_end = min(cursor + head_sec, limit)
            if window_end - cursor >= 0.5:
                windows.append((round(cursor, 3), round(window_end, 3)))
            cursor = window_end + demo_run_sec
    return windows


def directed_duration(transcript: Transcript, plan: RefStyleEditPlan) -> float:
    """Keep the rendered source tail, which can contain word decay after timing."""
    segment_end = max((segment.end for segment in plan.segments), default=0.0)
    return round(max(float(transcript.duration), segment_end), 3)


def flash_expr(plan: RefStyleEditPlan) -> str:
    ranges = [
        f"between(t,{segment.start:.2f},{segment.start + 0.09:.2f})"
        for segment in plan.segments[1:]
    ]
    return "+".join(ranges) if ranges else "0"


def whip_expr(plan: RefStyleEditPlan) -> str:
    """Short horizontal-smear windows straddling each cut for a whip-pan feel."""
    ranges = [
        f"between(t,{max(0.0, segment.start - 0.05):.2f},{segment.start + 0.07:.2f})"
        for segment in plan.segments[1:]
    ]
    return "+".join(ranges) if ranges else "0"


def handheld_motion() -> str:
    """Overscan + slow multi-frequency crop drift so the live frame breathes like
    a handheld camera. Applied to the live talk layer only, so locked graphics
    overlays and burned subtitles stay aligned."""
    margin = 1.045
    ax, ay = 8.0, 6.0
    sw = f"ceil(iw*{margin}/2)*2"
    sh = f"ceil(ih*{margin}/2)*2"
    x = (
        f"(iw-ow)/2 + {ax}*sin(2*PI*t/3.3) "
        f"+ {ax * 0.45:.2f}*sin(2*PI*t/1.27 + 0.7)"
    )
    y = (
        f"(ih-oh)/2 + {ay}*sin(2*PI*t/2.6 + 1.1) "
        f"+ {ay * 0.45:.2f}*sin(2*PI*t/0.97)"
    )
    return f"scale={sw}:{sh}:flags=lanczos,crop={W}:{H}:x='{x}':y='{y}'"


def smootherstep_expr(progress: str) -> str:
    return f"(({progress})*({progress})*({progress})*(({progress})*(6*({progress})-15)+10))"


def hook_zoom_chain() -> str:
    progress = f"min(max(it/{HOOK_ZOOM_OUT_SEC:.3f},0),1)"
    smooth = smootherstep_expr(progress)
    zoom_expr = f"1+({HOOK_ZOOM_DELTA:.5f}*(1-({smooth})))"
    return ",".join(
        [
            f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase:flags=lanczos",
            f"crop={W * 2}:{H * 2}:x='(iw-ow)/2':y='(ih-oh)/2'",
            (
                f"zoompan=z='{zoom_expr}':"
                "x='iw/2-(iw/zoom/2)':"
                "y='ih/2-(ih/zoom/2)':"
                f"d=1:fps={FPS}:s={W * 2}x{H * 2}"
            ),
            f"scale={W}:{H}:flags=lanczos",
            "setsar=1,format=yuv420p",
        ]
    )


def load_demo_tags(products: list[Path], manifest_path: Path | None) -> list[list[str]]:
    """Per-product keyword tags used to match a demo clip to what is being said.

    Manifest is a JSON object {clip_basename: [keyword, ...]}. Without a manifest
    (or for untagged clips) the product gets no tags and falls back to round-robin,
    preserving the old behaviour.
    """
    path = manifest_path
    if path is None and products:
        candidate = products[0].parent / "demo_tags.json"
        path = candidate if candidate.exists() else None
    by_name: dict[str, list[str]] = {}
    if path is not None and path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        by_name = {
            name: [str(w).lower().replace("ё", "е") for w in words]
            for name, words in raw.items()
        }
    return [by_name.get(product.name, []) for product in products]


def assign_demo_products(
    product_segments: list[RefStyleEditSegment], product_tags: list[list[str]]
) -> list[int]:
    """Pick, per demo segment, the product clip whose tags best match the words.

    Falls back to round-robin when a segment matches nothing, and avoids repeating
    the immediately-previous clip on ties so a run of demos still varies.
    """
    n = len(product_tags)
    assignments: list[int] = []
    if n == 0:
        return assignments
    rr = 0
    prev: int | None = None
    for segment in product_segments:
        text = (segment.text or "").lower().replace("ё", "е")
        scores = [sum(1 for kw in tags if kw and kw in text) for tags in product_tags]
        best = max(scores)
        if best > 0:
            candidates = [i for i, s in enumerate(scores) if s == best]
            choice = next((i for i in candidates if i != prev), candidates[0])
        else:
            choice = rr % n
            rr += 1
        assignments.append(choice)
        prev = choice
    return assignments


def render_base(
    out_path: Path,
    *,
    source: Path,
    product: Path | list[Path],
    music: Path,
    sfx_swish: Path,
    duration: float,
    plan: RefStyleEditPlan,
    emphasis: list[tuple[float, float]] | None = None,
    cut_times: list[float] | None = None,
    product_tags: list[list[str]] | None = None,
    forced_products: dict[float, str] | None = None,
    head_cutaways: bool = True,
) -> None:
    products = list(product) if isinstance(product, list) else [product]
    if not products:
        raise ValueError("at least one product demo asset is required")

    yellow_enable = enable_expr(plan, "format_2_framed_face")
    blue_enable = enable_expr(plan, "format_4_blue_demo")
    lower_enable = enable_expr(plan, "format_5_lower_demo_cta")
    close_enable = shot_close_enable(plan)
    flash = flash_expr(plan)
    whip = whip_expr(plan)
    # Mask the hard jump-cuts introduced by pause-tightening: a short horizontal
    # whip-smear straddling each cut hides the head/pose pop. cut_times are in the
    # final (tightened) timeline, so this never shifts durations or subtitle sync.
    if cut_times:
        cut_whip = "+".join(
            f"between(t,{max(0.0, c - 0.06):.2f},{c + 0.09:.2f})" for c in cut_times
        )
        whip = cut_whip if whip == "0" else f"({whip})+({cut_whip})"

    # Talking-head cutaways inside long demo beats: drop the blue cover + product
    # card during these windows so the live head shows. Kept few and calm (clean
    # cuts, no whip-smear) so the demo↔head transitions don't feel too dynamic.
    cutaways = demo_head_cutaways(plan) if head_cutaways else []
    cutaway_expr = "+".join(
        f"between(t,{start:.2f},{end:.2f})" for start, end in cutaways
    ) or "0"
    if cutaways:
        blue_enable = f"({blue_enable})*(1-({cutaway_expr}))"
    sfx_segments = list(plan.segments[1:5])
    music_input = 1 + len(products)
    sfx_input = music_input + 1
    product_segments = [
        segment
        for segment in plan.segments
        if segment.format_id in {"format_4_blue_demo", "format_5_lower_demo_cta"}
    ]
    tags = product_tags if product_tags is not None else [[] for _ in products]
    product_assignment = assign_demo_products(product_segments, tags)
    # Explicit per-segment product overrides (by segment start time): force a
    # specific clip on a beat regardless of tag matching.
    if forced_products:
        names = [p.name for p in products]
        for i, seg in enumerate(product_segments):
            clip = forced_products.get(round(seg.start, 2))
            if clip and clip in names:
                product_assignment[i] = names.index(clip)

    # Emphasis windows ("умный отклик") keep the blue demo background fully in
    # place over the talking head — we only stamp the grunge headline subtitles on
    # top. (Previously the blue cover was punched out here, exposing the raw live
    # head behind the demo, which is not wanted.)
    _ = emphasis

    tech = build_tech_chain(source)
    talk_crop = hook_zoom_chain()
    filter_parts = [
        f"[0:v]trim=start=0:duration={duration:.3f},setpts=PTS-STARTPTS,{tech},split=2[talkSrc][frameSrc]",
        f"[talkSrc]{talk_crop}[talkFull]",
        "[talkFull]split=2[talkMed][talkCropSrc]",
        (
            f"[talkCropSrc]crop={CLOSE_CROP_W}:{CLOSE_CROP_H}:{CLOSE_CROP_X}:{CLOSE_CROP_Y},"
            f"scale={W}:{H}:flags=lanczos,setsar=1[talkClose]"
        ),
        f"[talkMed][talkClose]overlay=0:0:enable='{close_enable}'[talkComposed]",
        f"[talkComposed]{handheld_motion()}[talk]",
        (
            # Clean rounded photo card like the reference: the photo fills the card
            # directly, with rounded corners and no polaroid-style cream mat.
            "[frameSrc]"
            "scale=872:1040:force_original_aspect_ratio=increase,"
            "crop=872:1040,setsar=1,"
            "eq=brightness=0.020:saturation=1.05:contrast=1.04,"
            f"format=rgba,{rounded_alpha(872, 1040, 42)},"
            "format=yuva420p[frame2]"
        ),
        (
            # Warm cloth-like yellow backdrop: cream grid and light grain, close
            # to the reference without heavy artificial bands. No drop shadow behind
            # the photo card — the rounded card sits cleanly on the grid.
            f"color=c=0xdfc52e:s={W}x{H}:r={FPS}:d={duration:.3f},"
            "format=rgba,noise=alls=5:allf=t+u,"
            "drawgrid=w=258:h=258:t=3:c=0xfff8d8@0.34,"
            "format=yuva420p[yellowBg]"
        ),
        (
            "[yellowBg]"
            "drawbox=x=70:y=415:w=872:h=1040:color=0xffffff@0.08:t=2"
            "[yellow]"
        ),
        (
            # Fully opaque blue grid backdrop: the talking head must NOT show through
            # behind the demo (previously aa=0.6 left it faintly visible at the edges).
            f"color=c=0x21aee4:s={W}x{H}:r={FPS}:d={duration:.3f},"
            "drawgrid=w=228:h=228:t=4:c=0xffffff@0.45,"
            "drawbox=x=0:y=0:w=1080:h=1920:color=0x0375a9@0.14:t=fill,"
            "noise=alls=6:allf=t+u,format=yuva420p[blue]"
        ),
        f"[talk][yellow]overlay=0:0:enable='{yellow_enable}'[v1]",
        f"[v1][frame2]overlay=70:415:enable='{yellow_enable}'[v2]",
        f"[v2][blue]overlay=0:0:enable='{blue_enable}'[v3]",
    ]

    demo4_layers: list[tuple[str, RefStyleEditSegment]] = []
    demo5_layers: list[tuple[str, RefStyleEditSegment]] = []
    for index, segment in enumerate(product_segments):
        input_index = 1 + (product_assignment[index] if product_assignment else index % len(products))
        segment_duration = max(0.10, segment.end - segment.start)
        if segment.format_id == "format_4_blue_demo":
            label = f"demo4_{index}"
            filter_parts.append(
                f"[{input_index}:v]trim=start=0:duration={segment_duration:.3f},"
                f"setpts=PTS-STARTPTS+{segment.start:.3f}/TB,fps={FPS},"
                # Big readable demo card: ~93% of frame width (was 820/1080) so the
                # product UI is large enough to read. The tall vertical screen
                # recording is fit to the card width; only the top/bottom of the
                # over-tall frame is trimmed, centered, to fill 1000x1500.
                "scale=1000:-2:flags=lanczos,"
                "crop=1000:1500:0:(ih-1500)/2,setsar=1,"
                "eq=brightness=0.020:saturation=1.04:contrast=1.03,"
                # No frame border — just light rounded corners on the demo area.
                f"format=rgba,{rounded_alpha(1000, 1500, 30)},"
                f"format=yuva420p[{label}]"
            )
            demo4_layers.append((label, segment))
        else:
            label = f"demo5_{index}"
            filter_parts.append(
                f"[{input_index}:v]trim=start=0:duration={segment_duration:.3f},"
                f"setpts=PTS-STARTPTS+{segment.start:.3f}/TB,fps={FPS},"
                "scale=980:1742:force_original_aspect_ratio=increase,"
                "crop=980:650:y=650,setsar=1,"
                "boxblur=0.4:1,eq=brightness=0.018:saturation=1.02:contrast=1.0,"
                # Rounded corners + mostly opaque (was aa=0.76, too see-through).
                f"format=rgba,{rounded_alpha(980, 650, 26)},"
                f"format=yuva420p,colorchannelmixer=aa=0.94[{label}]"
            )
            demo5_layers.append((label, segment))

    current = "v3"
    for index, (label, segment) in enumerate(demo4_layers):
        out_label = f"v4_{index}"
        # Card sits 50px lower so captions get a clear band above it; it is hidden
        # during head-cutaway windows (same windows the blue cover is dropped).
        demo_enable = f"between(t,{segment.start:.2f},{segment.end:.2f})"
        if cutaways:
            demo_enable = f"({demo_enable})*(1-({cutaway_expr}))"
        filter_parts.append(
            f"[{current}][{label}]overlay=40:336:eof_action=pass:"
            f"enable='{demo_enable}'[{out_label}]"
        )
        current = out_label
    filter_parts.append(f"[{current}]format=yuva420p[v5]")
    filter_parts.append(
        f"[v5]drawbox=x=50:y=1160:w=980:h=650:color=0xffffff@0.14:t=fill:enable='{lower_enable}',"
        f"drawbox=x=50:y=1160:w=980:h=650:color=0x000000@0.10:t=2:enable='{lower_enable}'[v6]"
    )
    current = "v6"
    for index, (label, segment) in enumerate(demo5_layers):
        out_label = f"v7_{index}"
        filter_parts.append(
            f"[{current}][{label}]overlay=50:1160:eof_action=pass:"
            f"enable='between(t,{segment.start:.2f},{segment.end:.2f})'[{out_label}]"
        )
        current = out_label
    filter_parts.extend(
        [
            f"[{current}]format=yuva420p[v7]",
            (
                "[v7]"
                f"avgblur=sizeX=64:sizeY=1:enable='{whip}',"
                f"drawbox=x=0:y=0:w=1080:h=1920:color=0xffffff@0.15:t=fill:enable='{flash}',"
                "format=yuv420p[vout]"
            ),
            (
                f"[0:a]atrim=start=0:duration={duration:.3f},asetpts=PTS-STARTPTS,"
                "loudnorm=I=-16:TP=-1.5:LRA=11,volume=2.0dB,"
                "aformat=sample_rates=48000:channel_layouts=stereo[voice]"
            ),
            (
                f"[{music_input}:a]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,volume=-28dB,"
                f"afade=t=in:st=0:d=0.55,afade=t=out:st={max(0.0, duration - 1.55):.2f}:d=1.1,"
                "aformat=sample_rates=48000:channel_layouts=stereo[music]"
            ),
        ]
    )

    if sfx_segments:
        filter_parts.append(f"[{sfx_input}:a]asplit={len(sfx_segments)}" + "".join(f"[sw{i}]" for i in range(len(sfx_segments))))
        sfx_labels = []
        for index, segment in enumerate(sfx_segments):
            delay = round(segment.start * 1000)
            label = f"sfx{index}"
            gain = -20 if segment.format_id == "format_5_lower_demo_cta" else -19
            filter_parts.append(
                f"[sw{index}]atrim=0:0.20,asetpts=PTS-STARTPTS,volume={gain}dB,"
                f"adelay={delay}|{delay},aformat=sample_rates=48000:channel_layouts=stereo[{label}]"
            )
            sfx_labels.append(f"[{label}]")
        filter_parts.append(
            "[voice][music]" + "".join(sfx_labels)
            + f"amix=inputs={2 + len(sfx_labels)}:duration=first:dropout_transition=0,alimiter=limit=0.95[aout]"
        )
    else:
        filter_parts.append("[voice][music]amix=inputs=2:duration=first:dropout_transition=0,alimiter=limit=0.95[aout]")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
    ]
    for product_path in products:
        cmd.extend(["-stream_loop", "-1", "-i", str(product_path)])
    cmd.extend(
        [
            "-stream_loop",
            "-1",
            "-i",
            str(music),
            "-stream_loop",
            "-1",
            "-i",
            str(sfx_swish),
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-t",
            f"{duration:.3f}",
            "-r",
            str(FPS),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-x264-params",
            "colorprim=bt709:colormatrix=bt709:transfer=bt709",
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
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    )
    run(cmd, "render directed visual base")


def burn_ass(base_path: Path, ass_path: Path, final_path: Path, fonts_dir: Path) -> None:
    vf = f"subtitles={q(str(ass_path))}:fontsdir={q(str(fonts_dir))}"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(base_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-r",
        str(FPS),
        "-x264-params",
        "colorprim=bt709:colormatrix=bt709:transfer=bt709",
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
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(final_path),
    ]
    run(cmd, "burn directed subtitles")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "new!.MOV")
    parser.add_argument("--transcript", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=ROOT / "output/ref-style-main/new_full_pipeline_v11.mp4")
    parser.add_argument(
        "--version",
        default=None,
        help="Render into a version subfolder of the output folder (e.g. v2), overwriting it. "
        "Default: create the next vN so previous renders are kept.",
    )
    parser.add_argument(
        "--no-version",
        action="store_true",
        help="Write straight to --output without a vN subfolder. For internal callers "
        "(e.g. reel_matrix) that render into a scratch path.",
    )
    parser.add_argument("--product", type=Path, action="append", default=None)
    parser.add_argument("--music", type=Path, default=ROOT / "assets/music/provocative.mp3")
    parser.add_argument("--sfx", type=Path, default=ROOT / "assets/sounds/swoosh.mp3")
    parser.add_argument("--fonts-dir", type=Path, default=ROOT / "assets/fonts")
    parser.add_argument("--clean-dir", type=Path, default=None)
    parser.add_argument(
        "--no-head-cutaways",
        action="store_true",
        help="Disable the short talking-head cutaways punched into long blue-demo "
        "beats, so each product screen holds steady (no flicker to the live head).",
    )
    parser.add_argument("--skip-clean-prelayer", action="store_true")
    parser.add_argument("--force-clean-prelayer", action="store_true")
    parser.add_argument("--tighten-pauses", action="store_true")
    parser.add_argument(
        "--product-overrides",
        type=Path,
        default=None,
        help=(
            "JSON list of [{start,end,format,clip}] to force a format and/or a "
            "specific product clip on the segments inside each time range."
        ),
    )
    parser.add_argument(
        "--demo-tags",
        type=Path,
        default=None,
        help=(
            "JSON manifest {clip.mp4: [keyword,...]} to match demo clips to what is "
            "said. Defaults to demo_tags.json next to the product clips. Untagged "
            "clips fall back to round-robin."
        ),
    )
    parser.add_argument(
        "--emphasis",
        action="append",
        default=None,
        metavar="PHRASE",
        help=(
            "space-separated phrase to give the big-face stamp treatment; "
            "repeatable. Defaults to the built-in EMPHASIS_PHRASES."
        ),
    )
    return parser.parse_args()


def resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def default_product_paths() -> list[Path]:
    demo_paths = [
        ROOT / "assets/broll_brand/sa_1_demo.mp4",
        ROOT / "assets/broll_brand/sa_2_demo.mp4",
        ROOT / "assets/broll_brand/sa_3_demo.mp4",
        ROOT / "assets/broll_brand/sa_4_demo.mp4",
    ]
    if all(path.exists() for path in demo_paths):
        return demo_paths
    return [ROOT / "assets/broll_brand/broll_product.mov"]


def main() -> None:
    args = parse_args()
    output = resolve_path(args.output)
    # Keep versions of the same video together: place the output file inside a
    # vN subfolder of its target folder instead of spawning sibling folders.
    # Internal callers (reel_matrix) pass --no-version to render to a scratch path.
    if not args.no_version:
        output = versioned_dir(output.parent, version=args.version) / output.name
    source = resolve_path(args.source)
    products = (
        [resolve_path(path) for path in args.product]
        if args.product
        else default_product_paths()
    )
    music = resolve_path(args.music)
    sfx = resolve_path(args.sfx)
    fonts_dir = resolve_path(args.fonts_dir)
    output.parent.mkdir(parents=True, exist_ok=True)

    clean_video: Path | None = None
    clean_transcript_path: Path | None = None
    tight_video: Path | None = None
    tight_transcript_path: Path | None = None
    tight_chunks: list[dict] = []
    render_source = source
    if args.skip_clean_prelayer:
        transcript_path = resolve_path(args.transcript) if args.transcript else output.with_suffix(".transcript.json")
        transcript = load_transcript(source, transcript_path)
    else:
        clean_dir = resolve_path(args.clean_dir) if args.clean_dir else output.with_name(f"{output.stem}_clean")
        clean_video, transcript, clean_transcript_path = prepare_clean_prelayer(
            source,
            clean_dir=clean_dir,
            source_transcript_path=resolve_path(args.transcript) if args.transcript else None,
            force=args.force_clean_prelayer,
        )
        render_source = clean_video
        if args.tighten_pauses:
            tight_video, transcript, tight_transcript_path, tight_chunks = tighten_pause_prelayer(
                clean_video,
                transcript,
                out_dir=clean_dir,
                force=args.force_clean_prelayer,
            )
            render_source = tight_video
        output.with_suffix(".transcript.json").write_text(
            transcript.model_dump_json(indent=2),
            encoding="utf-8",
        )

    plan = build_edit_plan(transcript, source=str(source))

    # Explicit product-insert overrides: force a format and/or a specific product
    # clip on every segment that falls inside an override's [start, end) range.
    forced_products: dict[float, str] = {}
    if args.product_overrides:
        from src.ref_style_director import FORMAT_SUBTITLE_MODES

        product_fmts = {"format_4_blue_demo", "format_5_lower_demo_cta"}
        overrides = json.loads(resolve_path(args.product_overrides).read_text(encoding="utf-8"))
        segs = list(plan.segments)
        for ov in overrides:
            lo = float(ov["start"])
            hi = float(ov.get("end", lo + 0.01))
            fmt = ov.get("format")
            clip = ov.get("clip")
            rebuilt: list[RefStyleEditSegment] = []
            for seg in segs:
                # No overlap -> keep as is.
                if seg.end <= lo + 0.05 or seg.start >= hi - 0.05:
                    rebuilt.append(seg)
                    continue
                # Split the segment so only the [lo, hi] middle takes the override;
                # the head part before lo keeps its original (talking-head) format.
                if seg.start < lo - 0.05:
                    rebuilt.append(seg.model_copy(update={"end": round(lo, 3)}))
                mid = seg.model_copy(update={
                    "start": round(max(seg.start, lo), 3),
                    "end": round(min(seg.end, hi), 3),
                })
                if fmt:
                    mid = mid.model_copy(update={
                        "format_id": fmt,
                        "subtitle_mode": FORMAT_SUBTITLE_MODES[fmt],
                        "product_demo": fmt in product_fmts,
                    })
                rebuilt.append(mid)
                if clip and mid.format_id in product_fmts:
                    forced_products[round(mid.start, 2)] = clip
                if seg.end > hi + 0.05:
                    rebuilt.append(seg.model_copy(update={"start": round(hi, 3)}))
            segs = rebuilt
        plan = plan.model_copy(update={"segments": segs})

        # Merge consecutive segments that share a format AND (for product beats)
        # the same forced clip into one. Otherwise a single overridden range that
        # spans several inherited sub-beats becomes several segments all forcing
        # the same clip -- and the renderer restarts that clip at start=0 on each,
        # which reads as the product frame jumping back mid-beat. Merging makes the
        # clip play once across the whole beat (and drops the in-beat whip/flash).
        merged: list[RefStyleEditSegment] = []
        for seg in plan.segments:
            if merged:
                prev = merged[-1]
                same_fmt = prev.format_id == seg.format_id
                contiguous = abs(seg.start - prev.end) < 0.6
                same_clip = forced_products.get(round(prev.start, 2)) == \
                    forced_products.get(round(seg.start, 2))
                if same_fmt and contiguous and same_clip:
                    merged[-1] = prev.model_copy(update={"end": seg.end})
                    continue
            merged.append(seg)
        plan = plan.model_copy(update={"segments": merged})

        # Close micro-gaps (silent pauses) between consecutive beats so the blue
        # cover + product card stay continuous instead of flashing to the live
        # head for a frame or two in the pause between two demo screens.
        closed: list[RefStyleEditSegment] = []
        segments = list(plan.segments)
        for i, seg in enumerate(segments):
            if i + 1 < len(segments):
                gap = segments[i + 1].start - seg.end
                if 0 < gap < 0.30:
                    seg = seg.model_copy(update={"end": round(segments[i + 1].start, 3)})
            closed.append(seg)
        plan = plan.model_copy(update={"segments": closed})

    duration = directed_duration(transcript, plan)

    ass_path = output.with_suffix(".ass")
    base_path = output.with_name(f"{output.stem}_base.mp4")
    plan_path = output.with_suffix(".edit_plan.json")
    prompt_path = output.with_suffix(".director_prompt.txt")
    diagnostics_path = output.with_suffix(".render_diagnostics.json")

    plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    prompt_path.write_text(
        build_director_prompt(transcript)
        + "\n\nCurrent guarded plan:\n"
        + json.dumps(json.loads(plan.model_dump_json()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    emphasis_phrases = (
        [phrase.split() for phrase in args.emphasis]
        if args.emphasis
        else EMPHASIS_PHRASES
    )
    write_ass(ass_path, transcript, plan, emphasis_phrases=emphasis_phrases)
    # Cut positions in the tightened (output) timeline = cumulative chunk durations,
    # one per join (the final chunk has no trailing cut). Used to mask head jumps.
    cut_times: list[float] = []
    if tight_chunks:
        acc = 0.0
        for chunk in tight_chunks[:-1]:
            acc += float(chunk["end"]) - float(chunk["start"])
            cut_times.append(round(acc, 3))
    product_tags = load_demo_tags(
        products, resolve_path(args.demo_tags) if args.demo_tags else None
    )
    render_base(
        base_path,
        source=render_source,
        product=products,
        music=music,
        sfx_swish=sfx,
        duration=duration,
        plan=plan,
        emphasis=find_phrase_windows(transcript, emphasis_phrases),
        cut_times=cut_times,
        product_tags=product_tags,
        forced_products=forced_products,
        head_cutaways=not args.no_head_cutaways,
    )
    # Record which clip landed on which demo line (for QA / review of sync).
    _demo_segments = [
        s for s in plan.segments
        if s.format_id in {"format_4_blue_demo", "format_5_lower_demo_cta"}
    ]
    _assignment = assign_demo_products(_demo_segments, product_tags)
    if forced_products:
        _names = [p.name for p in products]
        for i, seg in enumerate(_demo_segments):
            clip = forced_products.get(round(seg.start, 2))
            if clip and clip in _names:
                _assignment[i] = _names.index(clip)
    demo_assignment = [
        {
            "start": seg.start,
            "text": seg.text,
            "clip": products[_assignment[i]].name if _assignment else products[i % len(products)].name,
        }
        for i, seg in enumerate(_demo_segments)
    ]
    burn_ass(base_path, ass_path, output, fonts_dir)
    diagnostics_path.write_text(
        json.dumps(
            {
                "production_route": "custom_graphics",
                "source": str(source),
                "clean_prelayer": {
                    "enabled": not args.skip_clean_prelayer,
                    "final_clean": str(clean_video) if clean_video is not None else None,
                    "transcript": str(clean_transcript_path) if clean_transcript_path is not None else None,
                    "final_tight": str(tight_video) if tight_video is not None else None,
                    "tight_transcript": str(tight_transcript_path) if tight_transcript_path is not None else None,
                    "tight_chunks": tight_chunks,
                },
                "directed_render": {
                    "render_source": str(render_source),
                    "products": [str(path) for path in products],
                    "demo_assignment": demo_assignment,
                    "edit_plan": str(plan_path),
                    "ass": str(ass_path),
                    "base": str(base_path),
                    "final": str(output),
                    "duration_sec": duration,
                    "segments": [segment.model_dump() for segment in plan.segments],
                    "warnings": plan.warnings,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()
