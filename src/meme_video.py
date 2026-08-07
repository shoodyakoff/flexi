from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
W = 1080
H = 1920
FPS = 30
ROOT = Path(__file__).resolve().parent.parent
# Paths default to in-repo folders; override via .env (gitignored) for a local
# draft/publish/b-roll layout — e.g. MEME_DRAFT_ROOT=/path/to/your/drafts
DEFAULT_DRAFT_ROOT = Path(os.environ.get("MEME_DRAFT_ROOT", str(ROOT / "raw" / "meme")))
DEFAULT_PUBLISH_ROOT = Path(os.environ.get("MEME_PUBLISH_ROOT", str(ROOT / "output" / "meme")))
DEFAULT_BROLL_DIR = Path(os.environ.get("MEME_BROLL_DIR", str(ROOT / "assets" / "broll")))
DEFAULT_FONT_FILE = ROOT / "assets" / "fonts" / "BebasNeue-Cyrillic.ttf"


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int

    def drawbox_filter(self, color: str, *, alpha: float = 1.0, enable: str | None = None) -> str:
        filter_text = (
            f"drawbox=x={self.x}:y={self.y}:w={self.w}:h={self.h}:"
            f"color={color}@{alpha}:t=fill"
        )
        if enable:
            filter_text += f":enable='{enable}'"
        return filter_text


@dataclass(frozen=True)
class TextSpec:
    text: str
    max_chars_per_line: int = 24


@dataclass(frozen=True)
class LayoutProfile:
    name: str
    opening_duration: float
    outro_start: float
    opening_text_box: Box
    outro_text_box: Box
    outro_duration: float | None = None
    opening_max_chars_per_line: int = 21
    outro_max_chars_per_line: int = 24


@dataclass(frozen=True)
class MemeJob:
    index: int
    text: str
    broll_path: Path
    final_path: Path


BILLION_PROFILE = LayoutProfile(
    name="billion",
    opening_duration=3.5,
    outro_start=4.933333,
    outro_duration=5.766667,
    opening_text_box=Box(x=70, y=210, w=940, h=360),
    outro_text_box=Box(x=0, y=330, w=1080, h=320),
    opening_max_chars_per_line=28,
    outro_max_chars_per_line=22,
)


def _sort_key(path: Path) -> tuple[int, int | str, str]:
    stem = path.stem.strip()
    if stem.isdigit():
        return (0, int(stem), path.name.lower())
    match = re.match(r"^(\d+)", stem)
    if match:
        return (0, int(match.group(1)), path.name.lower())
    return (1, stem.lower(), path.name.lower())


def collect_broll_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        ),
        key=_sort_key,
    )


def safe_filename_stem(text: str, *, max_words: int = 8) -> str:
    normalized = text.lower().replace("ё", "е")
    words = re.findall(r"[0-9a-zа-я]+", normalized, flags=re.IGNORECASE)
    return "_".join(words[:max_words]) or "meme_video"


def punch_filename_stem(text: str, *, max_words: int = 5) -> str:
    quoted = re.findall(r"[«\"]([^»\"]+)[»\"]", text)
    candidate = quoted[-1] if quoted else text
    normalized = candidate.lower().replace("ё", "е")

    if not quoted:
        filler_patterns = [
            r"^\s*ты\s+(?:выиграл|получаешь)\s+миллиард\s*",
            r"^\s*но\s+больше\s+(?:никогда\s+)?не\s+(?:сможешь|можешь)\s*",
            r"^\s*больше\s+(?:никогда\s+)?не\s+(?:сможешь|можешь)\s*",
            r"^\s*нюхать\s+как\s*",
            r"^\s*стоять\s+в\s*",
            r"^\s*ловить\s*",
            r"^\s*слышать\s+как\s*",
            r"^\s*слышать\s*",
        ]
        changed = True
        while changed:
            changed = False
            for pattern in filler_patterns:
                updated = re.sub(pattern, "", normalized).strip()
                if updated != normalized:
                    normalized = updated
                    changed = True
        normalized = re.split(r"\bссылочка\b", normalized, maxsplit=1)[0].strip(" ,")

    words = re.findall(r"[0-9a-zа-я]+", normalized, flags=re.IGNORECASE)
    selected_words = words[:max_words]
    trailing_helpers = {"в", "на", "с", "со", "к", "ко", "от", "для", "и", "но", "как"}
    while selected_words and selected_words[-1] in trailing_helpers:
        selected_words.pop()
    return "_".join(selected_words) or safe_filename_stem(text, max_words=max_words)


def load_texts_file(path: Path) -> list[str]:
    texts: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^\s*(?:[-*]\s+|\d+[.)]\s*)", "", line).strip()
        if line:
            texts.append(line)
    return texts


def resolve_template_path(template: Path, *, draft_root: Path = DEFAULT_DRAFT_ROOT) -> Path:
    candidate = template if template.is_absolute() else draft_root / template
    if not candidate.exists():
        raise FileNotFoundError(candidate)
    return candidate


def build_jobs(*, texts: list[str], broll_files: list[Path], publish_dir: Path) -> list[MemeJob]:
    clean_texts = [text.strip() for text in texts if text.strip()]
    if not clean_texts:
        return []
    if not broll_files:
        raise ValueError("No b-roll files found for meme video generation")

    jobs: list[MemeJob] = []
    for i, text in enumerate(clean_texts, start=1):
        stem = punch_filename_stem(text)
        jobs.append(
            MemeJob(
                index=i,
                text=text,
                broll_path=broll_files[(i - 1) % len(broll_files)],
                final_path=publish_dir / f"{i:02d}_{stem}.mp4",
            )
        )
    return jobs


def render_text_lines(spec: TextSpec) -> list[str]:
    text = " ".join(spec.text.split())
    return textwrap.wrap(
        text,
        width=spec.max_chars_per_line,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]


def text_for_overlay(text: str, *, max_chars_per_line: int) -> str:
    return "\n".join(
        line.upper()
        for line in render_text_lines(
            TextSpec(text=text, max_chars_per_line=max_chars_per_line)
        )
    )


def _filter_path(path: Path) -> str:
    # ffmpeg filter values use ":" as a separator; command-list execution does
    # not shell-quote for us inside drawtext filter arguments.
    return str(path).replace("\\", "\\\\").replace(":", "\\:")


def _drawtext_filter(
    *,
    text_file: Path,
    font_file: Path,
    box: Box,
    font_size: int = 74,
    line_spacing: int = 8,
    borderw: int = 2,
    shadowx: int = 2,
    shadowy: int = 2,
    enable: str | None = None,
) -> str:
    filter_text = (
        "drawtext="
        f"fontfile={_filter_path(font_file)}:"
        f"textfile={_filter_path(text_file)}:"
        "fontcolor=white:"
        f"fontsize={font_size}:"
        f"line_spacing={line_spacing}:"
        f"borderw={borderw}:"
        "bordercolor=black@0.45:"
        f"shadowx={shadowx}:"
        f"shadowy={shadowy}:"
        "shadowcolor=black@0.35:"
        "text_align=C:"
        f"x=(w-text_w)/2:y={box.y}+({box.h}-text_h)/2"
    )
    if enable:
        filter_text += f":enable='{enable}'"
    return filter_text


def _video_normalize_filter() -> str:
    return (
        f"fps={FPS},"
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
        "setsar=1,format=yuv420p"
    )


def build_opening_command(
    *,
    broll_path: Path,
    template_path: Path,
    output_path: Path,
    profile: LayoutProfile,
    text_file: Path,
    font_file: Path,
) -> list[str]:
    duration = f"{profile.opening_duration:.3f}"
    visual = ",".join(
        [
            _video_normalize_filter(),
        ]
    )
    filter_complex = (
        f"[0:v]{visual}[v];"
        f"[1:a]atrim=start=0:duration={duration},asetpts=PTS-STARTPTS,"
        "aformat=sample_rates=48000:channel_layouts=stereo[a]"
    )
    inputs = [
        "-stream_loop",
        "-1",
        "-t",
        duration,
        "-i",
        str(broll_path),
        "-i",
        str(template_path),
    ]
    return [
        "ffmpeg",
        "-nostdin",
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def build_outro_command(
    *,
    template_path: Path,
    output_path: Path,
    profile: LayoutProfile,
    text_file: Path,
    font_file: Path,
) -> list[str]:
    visual = ",".join(
        [
            _video_normalize_filter(),
        ]
    )
    filter_complex = (
        f"[0:v]{visual}[v];"
        "[0:a]asetpts=PTS-STARTPTS,"
        "aformat=sample_rates=48000:channel_layouts=stereo[a]"
    )
    timing_args = ["-ss", f"{profile.outro_start:.3f}"]
    if profile.outro_duration is not None:
        timing_args.extend(["-t", f"{profile.outro_duration:.3f}"])
    return [
        "ffmpeg",
        "-nostdin",
        "-y",
        *timing_args,
        "-i",
        str(template_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def build_concat_command(concat_list: Path, output_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        # Rebase video timestamps to 0 — otherwise stream-copy concat starts the
        # video track ~0.02s late (B-frame delay) and the first frame renders as
        # a black square in players and thumbnails. PTS and DTS must shift by the
        # same amount; the combined `ts=` form flattens DTS onto PTS and breaks
        # B-frame decode order (visible stutter).
        "-bsf:v",
        "setts=pts=PTS-STARTPTS:dts=DTS-STARTPTS",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def build_qa_sheet_command(input_path: Path, output_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        "fps=1,scale=360:-1,tile=3x3",
        "-frames:v",
        "1",
        str(output_path),
    ]


def build_text_overlay_command(
    *,
    input_path: Path,
    output_path: Path,
    profile: LayoutProfile,
    opening_text_file: Path,
    outro_text_file: Path,
    font_file: Path,
) -> list[str]:
    opening_end = profile.opening_duration
    outro_end = profile.opening_duration + (profile.outro_duration or 3600.0)
    opening_enable = f"between(t,0.000,{opening_end:.3f})"
    outro_enable = f"between(t,{opening_end:.3f},{outro_end:.3f})"
    vf = ",".join(
        [
            _drawtext_filter(
                text_file=opening_text_file,
                font_file=font_file,
                box=profile.opening_text_box,
                font_size=66,
                line_spacing=6,
                enable=opening_enable,
            ),
            profile.outro_text_box.drawbox_filter("black", enable=outro_enable),
            _drawtext_filter(
                text_file=outro_text_file,
                font_file=font_file,
                box=profile.outro_text_box,
                font_size=80,
                line_spacing=8,
                enable=outro_enable,
            ),
            "setpts=PTS-STARTPTS",
            "format=yuv420p",
        ]
    )
    return [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        vf,
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "16",
        "-bf",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def write_concat_list(paths: list[Path], concat_list: Path) -> None:
    concat_list.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for path in paths:
        escaped = str(path.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    concat_list.write_text("\n".join(lines) + "\n")


def run_command(command: list[str], *, cwd: Path | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            + " ".join(command)
            + "\n\nSTDOUT:\n"
            + result.stdout
            + "\n\nSTDERR:\n"
            + result.stderr
        )


def probe_has_audio(path: Path) -> bool:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ffprobe failed: {path}")
    return bool(result.stdout.strip())


def render_job(
    *,
    job: MemeJob,
    template_path: Path,
    profile: LayoutProfile,
    work_dir: Path,
    font_file: Path,
    outro_text: str,
) -> Path:
    job_dir = work_dir / f"{job.index:02d}_{punch_filename_stem(job.text, max_words=5)}"
    job_dir.mkdir(parents=True, exist_ok=True)

    opening_text_file = job_dir / "opening_text.txt"
    outro_text_file = job_dir / "outro_text.txt"
    opening_text_file.write_text(
        text_for_overlay(job.text, max_chars_per_line=profile.opening_max_chars_per_line),
        encoding="utf-8",
    )
    outro_text_file.write_text(
        text_for_overlay(outro_text, max_chars_per_line=profile.outro_max_chars_per_line),
        encoding="utf-8",
    )

    opening_path = job_dir / "opening.mp4"
    outro_path = job_dir / "outro.mp4"
    concat_list = job_dir / "concat.txt"
    assembled_path = job_dir / "assembled.mp4"
    dedup_path = job_dir / "dedup_background.mp4"
    final_path = job_dir / "final.mp4"
    qa_sheet = job_dir / "qa_sheet.jpg"

    run_command(
        build_opening_command(
            broll_path=job.broll_path,
            template_path=template_path,
            output_path=opening_path,
            profile=profile,
            text_file=opening_text_file,
            font_file=font_file,
        )
    )
    run_command(
        build_outro_command(
            template_path=template_path,
            output_path=outro_path,
            profile=profile,
            text_file=outro_text_file,
            font_file=font_file,
        )
    )
    write_concat_list([opening_path, outro_path], concat_list)
    run_command(build_concat_command(concat_list, assembled_path))
    run_command(build_dedup_command(assembled_path, dedup_path))
    run_command(
        build_text_overlay_command(
            input_path=dedup_path,
            output_path=final_path,
            profile=profile,
            opening_text_file=opening_text_file,
            outro_text_file=outro_text_file,
            font_file=font_file,
        )
    )
    run_command(build_qa_sheet_command(final_path, qa_sheet))

    job.final_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(final_path, job.final_path)
    return final_path


def build_dedup_command(input_path: Path, output_path: Path) -> list[str]:
    vf = ",".join(
        [
            "lenscorrection=k1=0.04:k2=0.02",
            "crop=iw*0.95:ih*0.95:(iw*0.03):(ih*0.028)",
            f"scale={W}:{H}:flags=lanczos",
            "hue=h=4",
            "eq=brightness=0.008:contrast=1.015:saturation=1.02",
            "noise=c0s=4:c0f=t+u",
            "vignette=PI/6",
            "format=yuv420p",
        ]
    )
    return [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "19",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "18",
        "-bf",
        "3",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ac",
        "2",
        "-map_metadata",
        "-1",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
