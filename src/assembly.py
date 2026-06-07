from __future__ import annotations
from dataclasses import dataclass
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from rich.console import Console

from .assets import resolve_project_path
from .schemas import (
    BRollClip,
    Config,
    LookProfileConfig,
    MotionPreset,
    RenderPlan,
    RenderPlanSfxEvent,
    Transcript,
    Word,
)

console = Console()


@dataclass(frozen=True)
class FirstFrameReport:
    is_black: bool
    mean_luma: float
    black_pixel_ratio: float


@dataclass(frozen=True)
class CtaPunchCue:
    word: Word
    media_duration_sec: float


_ASS_TIME_RE = re.compile(r'(\d+):(\d{2}):(\d{2})\.(\d{2})')


def _ass_time_to_cs(m: re.Match) -> int:
    return int(m.group(1)) * 360000 + int(m.group(2)) * 6000 + int(m.group(3)) * 100 + int(m.group(4))


def _cs_to_ass_time(cs: int) -> str:
    cs = max(0, cs)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _copy_ass_to_temp(src: Path, prefix: str, lead_trim_sec: float = 0.0) -> Path:
    """libass is picky about some paths; copy ASS into a safe unique temp file.

    When lead_trim_sec > 0, all Dialogue timestamps are shifted back by that
    amount so they stay in sync after the video has been trimmed at the start.
    """
    with tempfile.NamedTemporaryFile(suffix=".ass", prefix=f"{prefix}_", delete=False, mode="w", encoding="utf-8") as tmp:
        tmp_path = Path(tmp.name)
        if lead_trim_sec <= 0.0:
            tmp.write(src.read_text(encoding="utf-8"))
            return tmp_path
        offset_cs = round(lead_trim_sec * 100)
        def _shift(field: str) -> str:
            return _ASS_TIME_RE.sub(lambda m: _cs_to_ass_time(_ass_time_to_cs(m) - offset_cs), field)
        for line in src.read_text(encoding="utf-8").splitlines(keepends=True):
            if line.startswith("Dialogue:"):
                parts = line.split(",", 9)
                if len(parts) >= 3:
                    parts[1] = _shift(parts[1])
                    parts[2] = _shift(parts[2])
                    line = ",".join(parts)
            tmp.write(line)
    return tmp_path


def _subtitles_filter_value(ass_path: Path, cfg: Config) -> str:
    fonts_dir = resolve_project_path(cfg.fonts.directory).resolve()
    return f"subtitles={ass_path}:fontsdir={fonts_dir}"


def _run(cmd: list[str], label: str) -> None:
    cmd_str = " ".join(cmd)
    console.log(f"[dim]$ {cmd_str}[/dim]")
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - t0
    if result.returncode != 0:
        console.log(f"[red]✗[/red] {label} failed ({elapsed:.1f}s)")
        raise RuntimeError(f"ffmpeg error in {label}:\n{result.stderr}")
    console.log(f"[green]✓[/green] {label} ({elapsed:.1f}s)")


# ──────────────────────────── color preservation ────────────────────────────
# Каждый libx264 проход без явных color tags дефолтно пишет метаданные BT.601
# и может менять range. Через 2-3 прохода это даёт заметное "выцветание".
# Решение: на КАЖДОМ энкоде писать одинаковые теги BT.709 limited-range.
# Пиксели при этом не меняются — меняется только то, как их интерпретирует
# плеер/следующий ffmpeg.

_COLOR_OUT_TAGS = [
    "-color_range", "tv",           # limited range (16-235) — стандарт для TikTok/Reels
    "-colorspace", "bt709",
    "-color_primaries", "bt709",
    "-color_trc", "bt709",
    "-pix_fmt", "yuv420p",          # фиксируем пиксель-формат, чтобы concat copy склеил
]

# Параметры, которые идут внутрь H.264 bitstream (чтобы не только контейнер,
# но и сам поток нёс правильные color flags).
_X264_COLOR_PARAMS = "colorprim=bt709:colormatrix=bt709:transfer=bt709"


def _probe_color_transfer(src: Path) -> str:
    """Читаем color_transfer исходника: HLG (arib-std-b67) / PQ (smpte2084) = HDR."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=color_transfer", "-of", "csv=p=0", str(src)],
        capture_output=True, text=True,
    )
    # ffprobe csv=p=0 может добавить хвостовую запятую; убираем
    return result.stdout.strip().rstrip(",").strip()


def _probe_duration(src: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(src)],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def _probe_leading_black_duration(src: Path, max_scan_sec: float = 0.75) -> float:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(src),
            "-t",
            f"{max_scan_sec:.3f}",
            "-vf",
            "blackdetect=d=0.02:pix_th=0.10",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0.0
    matches = re.findall(
        r"black_start:(?P<start>[0-9.]+)\s+black_end:(?P<end>[0-9.]+)",
        result.stderr,
    )
    for start_raw, end_raw in matches:
        start = float(start_raw)
        end = float(end_raw)
        if start <= 0.02 and 0 < end <= max_scan_sec:
            return end
    return 0.0


def inspect_first_frames(video: Path, *, frames: int = 3) -> FirstFrameReport:
    width = 32
    height = 32
    frame_size = width * height
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(video),
        "-vf",
        f"select='lt(n,{frames})',scale={width}:{height},format=gray",
        "-frames:v",
        str(frames),
        "-f",
        "rawvideo",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError("Unable to inspect first video frames")

    frame_count = max(1, min(frames, len(result.stdout) // frame_size))
    first = result.stdout[: frame_count * frame_size]
    pixel_count = len(first)
    mean_luma = sum(first) / pixel_count
    black_pixel_ratio = sum(1 for value in first if value <= 16) / pixel_count
    return FirstFrameReport(
        is_black=mean_luma <= 18 and black_pixel_ratio >= 0.90,
        mean_luma=mean_luma,
        black_pixel_ratio=black_pixel_ratio,
    )


def probe_clip_luma(src: Path, *, skip_sec: float = 1.0, scan_sec: float = 2.0) -> float:
    """Measure average luma over a stable window, skipping the leading auto-exposure ramp.
    Returns 128.0 on failure (neutral fallback — no dark-mode override)."""
    width, height = 32, 32
    frame_size = width * height
    n_frames = 8
    cmd = [
        "ffmpeg", "-v", "error",
        "-ss", f"{skip_sec:.3f}",
        "-i", str(src),
        "-t", f"{scan_sec:.3f}",
        "-vf", f"fps={n_frames / scan_sec:.2f},scale={width}:{height},format=gray",
        "-frames:v", str(n_frames),
        "-f", "rawvideo", "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or len(result.stdout) < frame_size:
        return 128.0
    n_got = min(n_frames, len(result.stdout) // frame_size)
    lumas = [
        sum(result.stdout[i * frame_size:(i + 1) * frame_size]) / frame_size
        for i in range(n_got)
    ]
    return sum(lumas) / len(lumas)


def _probe_exposure_ramp_duration(src: Path, scan_sec: float = 2.0, min_ramp_sec: float = 0.25) -> float:
    """Detect iPhone auto-exposure ramp at clip start (frames > 20% darker than stable).
    Returns trim duration in seconds, or 0.0 if no ramp detected."""
    width, height = 32, 32
    frame_size = width * height
    n_frames = 8
    interval = scan_sec / n_frames
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(src),
        "-t", f"{scan_sec:.3f}",
        "-vf", f"fps={n_frames / scan_sec:.2f},scale={width}:{height},format=gray",
        "-frames:v", str(n_frames),
        "-f", "rawvideo", "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or len(result.stdout) < frame_size * 3:
        return 0.0
    n_got = min(n_frames, len(result.stdout) // frame_size)
    lumas = [
        sum(result.stdout[i * frame_size:(i + 1) * frame_size]) / frame_size
        for i in range(n_got)
    ]
    stable = sum(lumas[n_got // 2:]) / max(1, n_got - n_got // 2)
    if stable < 25:
        return 0.0
    ramp_end_frame = 0
    for i, luma in enumerate(lumas):
        if luma < stable * 0.80:
            ramp_end_frame = i + 1
        else:
            break
    ramp_sec = ramp_end_frame * interval
    return ramp_sec if ramp_sec >= min_ramp_sec else 0.0


def _video_normalize_chain(src: Path, w: str, h: str) -> str:
    """Строим video filter chain до BT.709 SDR limited-range 8-bit.

    iPhone обычно снимает в HDR (BT.2020 HLG, 10-bit). Если просто кинуть в
    libx264 с pix_fmt=yuv420p — получим искажение яркости: HDR-пиксели
    обрежутся до 8-bit без пересчёта. Поэтому HDR исходник проходит через
    tonemap (hable — стандартный SDR-оператор), сохраняя визуальную яркость.
    SDR исходник проходит через простой scale с приведением матрицы/range.
    """
    transfer = _probe_color_transfer(src)
    is_hdr = transfer in ("arib-std-b67", "smpte2084")  # HLG / PQ

    if is_hdr:
        console.log(f"[blue]→[/blue] HDR source detected ({transfer}), tonemapping → SDR BT.709 (mobius + saturation boost)")
        # HDR→SDR через zscale+tonemap. Важные нюансы:
        # 1. npl=1000 для HLG (iPhone HLG reference = 1000 nit), не 100 —
        #    иначе пересветы сжимаются в кашу, картинка плоская.
        # 2. mobius — оператор, сохраняющий насыщенность лучше чем hable/reinhard.
        #    desat=0 отключает luma-based desaturation.
        # 3. eq=saturation=1.15 компенсирует сужение гаммы BT.2020 → BT.709
        #    (BT.709 покрывает ~70% BT.2020; без буста кожа/красный выглядят бледнее).
        # 4. eq=contrast=1.05 возвращает немного HDR-шного "punch".
        return ",".join([
            "zscale=t=linear:npl=1000",
            "format=gbrpf32le",
            "zscale=p=bt709",
            "tonemap=tonemap=mobius:desat=0",
            "zscale=t=bt709:m=bt709:r=limited",
            "format=yuv420p",
            "eq=saturation=1.15:contrast=1.05",
            f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        ])
    else:
        console.log(f"[blue]→[/blue] SDR source ({transfer or 'untagged'}), plain BT.709 normalize")
        return ",".join([
            f"scale={w}:{h}:force_original_aspect_ratio=decrease"
            f":in_range=auto:out_range=tv"
            f":in_color_matrix=auto:out_color_matrix=bt709",
            "format=yuv420p",
        ])


def _loudnorm_filter(cfg: Config) -> str:
    """Single-pass EBU R128 loudness normalization.
    I = integrated loudness target (LUFS, -16 = YouTube/podcast standard).
    TP = true-peak ceiling (dBTP).
    LRA = loudness range target.
    All audio (hook, cta, voiceover) passes through this → unified level.
    """
    return (
        f"loudnorm=I={cfg.audio.loudnorm_target_lufs}"
        f":TP={cfg.audio.loudnorm_true_peak}"
        f":LRA={cfg.audio.loudnorm_lra}"
    )


def _spoken_audio_filter(cfg: Config, *, gain_db: float) -> str:
    parts = [
        _loudnorm_filter(cfg),
    ]
    if abs(gain_db) > 0.001:
        parts.append(f"volume={gain_db}dB")
    parts.append(f"aformat=sample_rates={cfg.audio.sample_rate}:channel_layouts=stereo")
    return ",".join(parts)


def _sfx_generator_chain(
    event: RenderPlanSfxEvent,
    cfg: Config,
    *,
    sample_input_index: int | None = None,
) -> str:
    delay_ms = max(0, round(event.timeline_start * 1000))
    fade_out_start = max(0.0, event.duration_sec - 0.04)

    if event.sample_path is not None and sample_input_index is not None:
        source = (
            f"[{sample_input_index}:a]"
            f"atrim=0:{event.duration_sec:.3f},asetpts=PTS-STARTPTS"
        )
    elif event.generator == "brand_ping":
        source = f"sine=frequency=980:sample_rate={cfg.audio.sample_rate}:duration={event.duration_sec:.3f}"
    elif event.generator == "transition_soft":
        source = f"sine=frequency=420:sample_rate={cfg.audio.sample_rate}:duration={event.duration_sec:.3f}"
    elif event.generator == "punch_click":
        source = (
            f"sine=frequency=860:sample_rate={cfg.audio.sample_rate}:duration={event.duration_sec:.3f},"
            "highpass=f=520"
        )
    elif event.generator == "cta_lift":
        source = (
            f"sine=frequency=640:sample_rate={cfg.audio.sample_rate}:duration={event.duration_sec:.3f},"
            "lowpass=f=1600"
        )
    else:
        source = f"sine=frequency=720:sample_rate={cfg.audio.sample_rate}:duration={event.duration_sec:.3f}"

    return (
        f"{source},aformat=sample_rates={cfg.audio.sample_rate}:channel_layouts=stereo,"
        f"volume={event.gain_db}dB,"
        f"afade=t=in:st=0:d=0.01,"
        f"afade=t=out:st={fade_out_start:.3f}:d=0.04,"
        f"adelay={delay_ms}|{delay_ms}[sfx{event.sequence_index}]"
    )


def _motion_progress_expr(duration: float) -> str:
    safe_duration = max(duration, 0.001)
    return f"min(max(t/{safe_duration:.3f},0),1)"


def _zoompan_progress_expr(duration: float) -> str:
    safe_duration = max(duration, 0.001)
    return f"min(max(it/{safe_duration:.3f},0),1)"


def _smootherstep_expr(progress: str) -> str:
    return f"(({progress})*({progress})*({progress})*(({progress})*(6*({progress})-15)+10))"


def _center_zoompan_chain(
    *,
    zoom_in: bool,
    width: int,
    height: int,
    duration_sec: float,
    delta: float,
    fps: int,
) -> str:
    progress = _smootherstep_expr(_zoompan_progress_expr(duration_sec))
    if zoom_in:
        zoom_expr = f"1+({delta:.5f}*({progress}))"
    else:
        zoom_expr = f"1+({delta:.5f}*(1-({progress})))"
    work_width = width * 2
    work_height = height * 2
    return ",".join([
        f"fps={fps}",
        f"scale={work_width}:{work_height}:flags=lanczos",
        (
            f"zoompan=z='{zoom_expr}':"
            "x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)':"
            f"d=1:fps={fps}:s={work_width}x{work_height}"
        ),
        f"scale={width}:{height}:flags=lanczos",
        "setsar=1",
    ])


_CTA_STOPWORDS = {
    "а",
    "без",
    "в",
    "во",
    "для",
    "и",
    "или",
    "к",
    "ко",
    "можно",
    "на",
    "не",
    "но",
    "о",
    "об",
    "по",
    "с",
    "со",
    "то",
    "это",
}

_CTA_ACTION_PRIORITY_STEM_WEIGHTS = {
    "ссылк": 4.0,
    "отправ": 3.5,
    "подел": 3.5,
    "попроб": 2.0,
}

_CTA_SECONDARY_PRIORITY_STEMS = (
    "описан",
    "шапк",
    "профил",
)

_CTA_TAIL_PENALTY_STEMS = (
    "дава",
    "смотр",
    "пиш",
)


def _clean_word_token(word: str) -> str:
    return re.sub(r"[^\wа-яА-ЯёЁ-]", "", word, flags=re.UNICODE).lower()


def _cta_punch_word_score(word: Word, speech_duration: float) -> tuple[float, float, float, float]:
    token = _clean_word_token(word.word)
    center_sec = (word.start + word.end) / 2
    progress = center_sec / max(speech_duration, 0.001)
    centrality = max(0.0, 1.0 - (abs(progress - 0.5) / 0.5))

    priority = 0.0
    for stem, weight in _CTA_ACTION_PRIORITY_STEM_WEIGHTS.items():
        if token.startswith(stem):
            priority = weight
            break
    if priority == 0.0 and any(token.startswith(stem) for stem in _CTA_SECONDARY_PRIORITY_STEMS):
        priority = 1.0

    tail_penalty = 0.0
    if progress >= 0.82:
        tail_penalty += 1.0
    if any(token.startswith(stem) for stem in _CTA_TAIL_PENALTY_STEMS):
        tail_penalty += 3.0

    return (
        priority - tail_penalty,
        centrality,
        -abs(progress - 0.5),
        -word.start,
    )


def _cta_lead_trim_sec(
    transcript: Transcript | None,
    media_duration_sec: float,
    *,
    visual_preroll_sec: float = 0.08,
) -> float:
    if transcript is None or not transcript.words:
        return 0.0
    first_word_start = transcript.words[0].start
    trim = max(0.0, first_word_start - visual_preroll_sec)
    max_trim = max(0.0, media_duration_sec - 0.25)
    return min(trim, max_trim)


def _cta_duration_limit_sec(
    transcript: Transcript | None,
    *,
    lead_trim_sec: float,
    media_duration_sec: float,
    cfg: Config,
) -> float:
    post_lead_duration = max(0.001, media_duration_sec - lead_trim_sec)
    max_duration = cfg.render_guardrails.cta_target_max_sec
    if transcript is None or not transcript.words:
        return min(post_lead_duration, max_duration)

    words_after_trim = [
        word
        for word in transcript.words
        if word.end > lead_trim_sec + 0.01
    ]
    if not words_after_trim:
        return min(post_lead_duration, max_duration)

    last_spoken_end = max(word.end for word in words_after_trim)
    if last_spoken_end <= lead_trim_sec:
        return min(post_lead_duration, max_duration)

    soft_tail_sec = cfg.render_guardrails.cta_soft_tail_sec
    spoken_limit = last_spoken_end - lead_trim_sec + soft_tail_sec
    return min(post_lead_duration, max(0.25, spoken_limit))


def _select_cta_punch_cue(
    transcript: Transcript,
    media_duration_sec: float,
) -> CtaPunchCue | None:
    if not transcript.words:
        return None

    speech_duration = transcript.duration or transcript.words[-1].end
    latest_end = max(0.0, media_duration_sec - 0.18)
    candidates = [
        word
        for word in transcript.words
        if word.end <= latest_end
        and len(_clean_word_token(word.word)) >= 3
        and _clean_word_token(word.word) not in _CTA_STOPWORDS
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda word: _cta_punch_word_score(word, speech_duration))
    return CtaPunchCue(word=best, media_duration_sec=media_duration_sec)


def _cta_punch_zoompan_chain(
    *,
    width: int,
    height: int,
    duration_sec: float,
    punch_start_sec: float,
    punch_duration_sec: float,
    fps: int,
    base_delta: float = 0.0700,
    punch_delta: float = 0.1150,
) -> str:
    base_progress = _smootherstep_expr(_zoompan_progress_expr(duration_sec))
    punch_progress = _smootherstep_expr(
        f"min(max((it-{punch_start_sec:.3f})/{max(punch_duration_sec, 0.001):.3f},0),1)"
    )
    zoom_expr = (
        f"1+({base_delta:.5f}*(1-({base_progress})))"
        f"+({punch_delta:.5f}*({punch_progress}))"
    )
    work_width = width * 2
    work_height = height * 2
    return ",".join([
        f"fps={fps}",
        f"scale={work_width}:{work_height}:flags=lanczos",
        (
            f"zoompan=z='{zoom_expr}':"
            "x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)':"
            f"d=1:fps={fps}:s={work_width}x{work_height}"
        ),
        f"scale={width}:{height}:flags=lanczos",
        "setsar=1",
    ])


def _motion_filter_chain(
    preset: MotionPreset,
    *,
    width: int,
    height: int,
    duration_sec: float,
    strength: float,
    cfg: Config,
) -> str:
    progress = _motion_progress_expr(duration_sec)

    if preset == "zoom_in_soft":
        delta = cfg.motion.zoom_scale_delta * strength
        return _center_zoompan_chain(
            zoom_in=True,
            width=width,
            height=height,
            duration_sec=duration_sec,
            delta=delta,
            fps=cfg.video.fps,
        )

    if preset == "zoom_out_soft":
        delta = cfg.motion.zoom_scale_delta * strength
        return _center_zoompan_chain(
            zoom_in=False,
            width=width,
            height=height,
            duration_sec=duration_sec,
            delta=delta,
            fps=cfg.video.fps,
        )

    if preset in {"drift_left", "drift_right"}:
        delta = cfg.motion.drift_scale_delta * strength
        scaled_w = max(width, round(width * (1.0 + delta)))
        scaled_h = max(height, round(height * (1.0 + delta)))
        max_x = max(0, scaled_w - width)
        y_center = max(0.0, (scaled_h - height) / 2)
        x_expr = (
            f"'{max_x:.3f}*(1-{progress})'"
            if preset == "drift_left"
            else f"'{max_x:.3f}*{progress}'"
        )
        return ",".join([
            f"scale={scaled_w}:{scaled_h}",
            f"crop={width}:{height}:x={x_expr}:y='{y_center:.3f}'",
        ])

    if preset in {"push_up", "push_down"}:
        delta = cfg.motion.vertical_scale_delta * strength
        scaled_w = max(width, round(width * (1.0 + delta)))
        scaled_h = max(height, round(height * (1.0 + delta)))
        x_center = max(0.0, (scaled_w - width) / 2)
        max_y = max(0, scaled_h - height)
        y_expr = (
            f"'{max_y:.3f}*(1-{progress})'"
            if preset == "push_up"
            else f"'{max_y:.3f}*{progress}'"
        )
        return ",".join([
            f"scale={scaled_w}:{scaled_h}",
            f"crop={width}:{height}:x='{x_center:.3f}':y={y_expr}",
        ])

    return "null"


def _look_filter_chain(profile: LookProfileConfig | None) -> str | None:
    if profile is None:
        return None

    filters = [
        (
            "eq="
            f"brightness={profile.brightness:.3f}:"
            f"contrast={profile.contrast:.3f}:"
            f"saturation={profile.saturation:.3f}:"
            f"gamma={profile.gamma:.3f}"
        )
    ]
    if profile.sharpness > 0:
        filters.append(
            "unsharp="
            "luma_msize_x=5:"
            "luma_msize_y=5:"
            f"luma_amount={profile.sharpness:.3f}"
        )
    if profile.lut_file:
        lut_path = resolve_project_path(profile.lut_file).resolve()
        if not lut_path.exists():
            raise FileNotFoundError(f"look profile lut_file not found: {lut_path}")
        # Escape ffmpeg filter-arg specials in the path (\, :, ').
        escaped = (
            str(lut_path)
            .replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "\\'")
        )
        filters.append(f"lut3d='{escaped}'")
    return ",".join(filters)


def normalize_clip(
    src: Path,
    out: Path,
    cfg: Config,
    burn_ass: Path | None = None,
    *,
    motion_preset: MotionPreset | None = None,
    motion_duration_sec: float | None = None,
    motion_strength: float | None = None,
    look_profile: str | None = None,
    lead_trim_sec: float = 0.0,
    duration_limit_sec: float | None = None,
) -> Path:
    """Scale to 9:16 с BT.709 limited-range нормализацией, set fps, re-encode
    audio с loudnorm, optionally burn subtitles. CRF = intermediate (высокое
    качество для промежуточного файла)."""
    w, h = cfg.video.resolution.split("x")
    width = int(w)
    height = int(h)

    vf_parts = []
    if lead_trim_sec > 0:
        vf_parts.append(f"trim=start={lead_trim_sec:.3f}")
    vf_parts.extend([
        "setpts=PTS-STARTPTS",
        _video_normalize_chain(src, w, h),
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
    ])
    if motion_preset is not None and motion_duration_sec is not None:
        vf_parts.append(
            _motion_filter_chain(
                motion_preset,
                width=width,
                height=height,
                duration_sec=motion_duration_sec,
                strength=motion_strength or 1.0,
                cfg=cfg,
            )
        )
    look_filter = _look_filter_chain(cfg.look_profiles.get(look_profile) if look_profile else None)
    if look_filter is not None:
        vf_parts.append(look_filter)
    tmp_ass_for_cleanup: Path | None = None
    if burn_ass is not None:
        # Separate temp files per segment remove any ambiguity between hook/cta subtitles.
        tmp_ass_for_cleanup = _copy_ass_to_temp(burn_ass, src.stem, lead_trim_sec)
        vf_parts.append(_subtitles_filter_value(tmp_ass_for_cleanup, cfg))
    if duration_limit_sec is not None:
        vf_parts.append(f"trim=duration={max(duration_limit_sec, 0.001):.3f}")
        vf_parts.append("setpts=PTS-STARTPTS")
    vf_parts.append("setsar=1")

    af_parts = []
    if lead_trim_sec > 0:
        af_parts.append(f"atrim=start={lead_trim_sec:.3f}")
    af_parts.append("asetpts=PTS-STARTPTS")
    af_parts.append(_spoken_audio_filter(cfg, gain_db=0.0))
    if duration_limit_sec is not None:
        af_parts.append(f"atrim=duration={max(duration_limit_sec, 0.001):.3f}")
        af_parts.append("asetpts=PTS-STARTPTS")

    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", ",".join(vf_parts),
        "-af", ",".join(af_parts),
        "-r", str(cfg.video.fps),
        "-c:v", cfg.video.codec,
        "-crf", str(cfg.video.crf_intermediate),
        "-x264-params", _X264_COLOR_PARAMS,
        *_COLOR_OUT_TAGS,
        "-c:a", "aac",
        "-ar", str(cfg.audio.sample_rate),
        "-ac", "2",
        str(out),
    ]
    try:
        _run(cmd, f"normalize {src.name}")
    finally:
        if tmp_ass_for_cleanup is not None:
            tmp_ass_for_cleanup.unlink(missing_ok=True)
    return out


def _normalize_cta_clip(
    src: Path,
    out: Path,
    cfg: Config,
    *,
    transcript: Transcript | None,
    burn_ass: Path | None = None,
    look_profile: str | None = None,
) -> Path:
    cta_duration_sec = _probe_duration(src)
    lead_trim_sec = _cta_lead_trim_sec(transcript, cta_duration_sec)
    normalized_duration_sec = _cta_duration_limit_sec(
        transcript,
        lead_trim_sec=lead_trim_sec,
        media_duration_sec=cta_duration_sec,
        cfg=cfg,
    )
    cue = (
        _select_cta_punch_cue(transcript, lead_trim_sec + normalized_duration_sec)
        if transcript is not None
        else None
    )
    swoosh_path = (
        resolve_project_path(cfg.hook_montage.sfx.swoosh)
        if cfg.hook_montage.sfx.swoosh
        else None
    )
    if cue is None or swoosh_path is None or not swoosh_path.exists():
        return normalize_clip(
            src,
            out,
            cfg,
            burn_ass=burn_ass,
            motion_preset="zoom_out_soft",
            motion_duration_sec=normalized_duration_sec,
            motion_strength=0.85,
            look_profile=look_profile,
            lead_trim_sec=lead_trim_sec,
            duration_limit_sec=normalized_duration_sec,
        )

    w, h = cfg.video.resolution.split("x")
    width = int(w)
    height = int(h)
    punch_start = max(0.0, cue.word.start - lead_trim_sec - 0.08)
    punch_duration = max(0.18, min(0.38, cue.word.end - punch_start + 0.10))
    swoosh_start = max(0.0, cue.word.start - lead_trim_sec - 0.10)
    swoosh_duration = min(
        _probe_duration(swoosh_path),
        max(0.12, normalized_duration_sec - swoosh_start),
    )
    delay_ms = max(0, round(swoosh_start * 1000))
    duck_start = max(0.0, swoosh_start - 0.04)
    duck_end = min(normalized_duration_sec, swoosh_start + min(swoosh_duration, 0.44))
    swoosh_gain_db = min(0.0, cfg.hook_montage.sfx.swoosh_gain_db + 5.0)

    vf_parts = []
    if lead_trim_sec > 0:
        vf_parts.append(f"trim=start={lead_trim_sec:.3f}")
    vf_parts.extend([
        "setpts=PTS-STARTPTS",
        _video_normalize_chain(src, w, h),
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
        _cta_punch_zoompan_chain(
            width=width,
            height=height,
            duration_sec=normalized_duration_sec,
            punch_start_sec=punch_start,
            punch_duration_sec=punch_duration,
            fps=cfg.video.fps,
        ),
    ])
    look_filter = _look_filter_chain(cfg.look_profiles.get(look_profile) if look_profile else None)
    if look_filter is not None:
        vf_parts.append(look_filter)
    tmp_ass_for_cleanup: Path | None = None
    if burn_ass is not None:
        tmp_ass_for_cleanup = _copy_ass_to_temp(burn_ass, src.stem, lead_trim_sec)
        vf_parts.append(_subtitles_filter_value(tmp_ass_for_cleanup, cfg))
    vf_parts.extend([
        f"trim=duration={normalized_duration_sec:.3f}",
        "setpts=PTS-STARTPTS",
        "setsar=1",
    ])

    filter_complex = ";".join([
        f"[0:v]{','.join(vf_parts)}[vout]",
        f"[0:a]atrim=start={lead_trim_sec:.3f},asetpts=PTS-STARTPTS,"
        f"{_spoken_audio_filter(cfg, gain_db=0.0)},"
        f"volume=0.78:enable='between(t,{duck_start:.3f},{duck_end:.3f})',"
        f"atrim=duration={normalized_duration_sec:.3f},asetpts=PTS-STARTPTS[base]",
        f"[1:a]atrim=start=0:duration={swoosh_duration:.3f},asetpts=PTS-STARTPTS,"
        f"aformat=sample_rates={cfg.audio.sample_rate}:channel_layouts=stereo,"
        f"afade=t=out:st={max(0.0, swoosh_duration - 0.10):.3f}:d=0.10,"
        f"volume={swoosh_gain_db:.2f}dB,adelay={delay_ms}|{delay_ms}[cta_swoosh]",
        "[base][cta_swoosh]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]",
    ])

    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-i", str(swoosh_path),
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-r", str(cfg.video.fps),
        "-c:v", cfg.video.codec,
        "-crf", str(cfg.video.crf_intermediate),
        "-x264-params", _X264_COLOR_PARAMS,
        *_COLOR_OUT_TAGS,
        "-c:a", "aac",
        "-ar", str(cfg.audio.sample_rate),
        "-ac", "2",
        str(out),
    ]
    try:
        _run(cmd, f"normalize CTA with punch on '{cue.word.word.strip()}'")
    finally:
        if tmp_ass_for_cleanup is not None:
            tmp_ass_for_cleanup.unlink(missing_ok=True)
    return out


def _clip_duration(clip: BRollClip) -> float:
    return max(0.0, clip.end - clip.start)


def _alternating_broll_motion_preset(index: int) -> MotionPreset:
    return "zoom_out_soft" if index % 2 == 0 else "zoom_in_soft"


# Map our TransitionKind onto ffmpeg xfade `transition=` names. "cut" never
# reaches here (it short-circuits in _broll_transitions_enabled).
_XFADE_NAME_BY_KIND = {
    "fade": "fade",
    "dissolve": "fade",
    "dipblack": "fadeblack",
    "dipwhite": "fadewhite",
    "wipeleft": "wipeleft",
    "wiperight": "wiperight",
    "wipeup": "wipeup",
    "wipedown": "wipedown",
    "slideleft": "slideleft",
    "slideright": "slideright",
    "slideup": "slideup",
    "slidedown": "slidedown",
}


def _broll_transitions_enabled(cfg: Config) -> bool:
    transition = cfg.transition
    return (
        transition.enabled
        and transition.kind != "cut"
        and transition.duration_sec > 0
    )


def _should_flash_broll_cut(index: int, cfg: Config) -> bool:
    # Flash and xfade are competing transition styles — when xfade b-roll
    # transitions are on, skip the flash so cuts don't get double-treated.
    if _broll_transitions_enabled(cfg):
        return False
    return index > 0 and cfg.hook_montage.transition.enabled and index % 2 == 1


def _is_transition_eligible(
    previous_clip: BRollClip,
    next_clip: BRollClip,
    cfg: Config,
) -> bool:
    transition = cfg.transition
    if not _broll_transitions_enabled(cfg):
        return False
    if transition.skip_product_insert and (
        previous_clip.asset_id == "product_insert" or next_clip.asset_id == "product_insert"
    ):
        return False
    if (
        _clip_duration(previous_clip) < transition.min_clip_duration_sec
        or _clip_duration(next_clip) < transition.min_clip_duration_sec
    ):
        return False
    return min(_clip_duration(previous_clip), _clip_duration(next_clip)) >= transition.duration_sec * 3


def _concat_broll_video(normalized_clips: list[Path], out_dir: Path, cfg: Config) -> Path:
    concat_list = out_dir / "broll_concat.txt"
    concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in normalized_clips))
    concat_out = out_dir / "broll_raw.mp4"

    cmd = ["ffmpeg", "-y"]
    for path in normalized_clips:
        cmd.extend(["-i", str(path)])

    chains = [
        f"[{index}:v]fps={cfg.video.fps},setpts=PTS-STARTPTS,setsar=1[v{index}]"
        for index in range(len(normalized_clips))
    ]
    chains.append(
        "".join(f"[v{index}]" for index in range(len(normalized_clips)))
        + f"concat=n={len(normalized_clips)}:v=1:a=0[vout]"
    )

    cmd.extend([
        "-filter_complex", ";".join(chains),
        "-map", "[vout]",
        "-r", str(cfg.video.fps),
        "-c:v", cfg.video.codec,
        "-crf", str(cfg.video.crf_intermediate),
        "-x264-params", _X264_COLOR_PARAMS,
        *_COLOR_OUT_TAGS,
        "-an",
        str(concat_out),
    ])
    _run(cmd, "concat broll")
    return concat_out


def _concat_broll_with_xfade(
    normalized_clips: list[Path],
    eligible: list[bool],
    out_dir: Path,
    cfg: Config,
) -> Path:
    """Concatenate b-roll, applying an xfade transition at each eligible
    boundary and a hard cut elsewhere.

    Total duration is preserved: clips on the "from" side of an eligible
    boundary already carry `transition.duration_sec` of extra tail (added by
    build_broll_section), which the xfade overlap consumes — so the timeline
    stays in sync with the voiceover and burned subtitles.
    """
    if len(normalized_clips) < 2 or not any(eligible):
        return _concat_broll_video(normalized_clips, out_dir, cfg)

    concat_out = out_dir / "broll_raw.mp4"
    transition_duration = cfg.transition.duration_sec
    xfade_name = _XFADE_NAME_BY_KIND[cfg.transition.kind]
    durations = [_probe_duration(path) for path in normalized_clips]

    cmd = ["ffmpeg", "-y"]
    for path in normalized_clips:
        cmd += ["-i", str(path)]

    # Common fps/timebase/SAR so xfade and concat accept every input.
    chains = [
        f"[{index}:v]fps={cfg.video.fps},settb=AVTB,setpts=PTS-STARTPTS,setsar=1[v{index}]"
        for index in range(len(normalized_clips))
    ]

    cur_label = "v0"
    cur_len = durations[0]
    applied = 0
    for i in range(1, len(normalized_clips)):
        out_label = f"x{i}"
        if eligible[i - 1]:
            offset = max(0.0, cur_len - transition_duration)
            chains.append(
                f"[{cur_label}][v{i}]xfade=transition={xfade_name}:"
                f"duration={transition_duration:.3f}:offset={offset:.3f}[{out_label}]"
            )
            cur_len = cur_len + durations[i] - transition_duration
            applied += 1
        else:
            chains.append(f"[{cur_label}][v{i}]concat=n=2:v=1:a=0[{out_label}]")
            cur_len = cur_len + durations[i]
        cur_label = out_label

    cmd += [
        "-filter_complex", ";".join(chains),
        "-map", f"[{cur_label}]",
        "-r", str(cfg.video.fps),
        "-c:v", cfg.video.codec,
        "-crf", str(cfg.video.crf_intermediate),
        "-x264-params", _X264_COLOR_PARAMS,
        *_COLOR_OUT_TAGS,
        "-an",
        str(concat_out),
    ]
    _run(cmd, f"concat broll with {applied} {cfg.transition.kind} transition(s)")
    console.log(
        f"[green]✓[/green] Applied {applied} b-roll {cfg.transition.kind} transition(s)"
    )
    return concat_out


def _freeze_pad_tail(src: Path, out: Path, duration: float, cfg: Config) -> Path:
    """Extend a clip by freezing its last frame for `duration` seconds.

    Used as a fallback when a source asset lacks the spare footage needed to
    carry an xfade overlap. The frozen tail lives entirely inside the
    transition region, where it is dissolved/wiped out, so it is invisible.
    """
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", f"tpad=stop_mode=clone:stop_duration={duration:.3f}",
        "-r", str(cfg.video.fps),
        "-c:v", cfg.video.codec,
        "-crf", str(cfg.video.crf_intermediate),
        "-x264-params", _X264_COLOR_PARAMS,
        *_COLOR_OUT_TAGS,
        "-an",
        str(out),
    ]
    _run(cmd, f"freeze-pad broll tail (+{duration:.2f}s)")
    return out


def build_broll_section(
    render_plan: RenderPlan,
    voiceover_path: Path,
    subtitles_path: Path,
    out_dir: Path,
    cfg: Config,
    *,
    section_name: str = "broll_section_clean.mp4",
) -> Path:
    w, h = cfg.video.resolution.split("x")
    normalized_clips: list[Path] = []
    clips = [
        BRollClip(
            asset_id=clip.asset_id,
            file=clip.file,
            start=clip.source_start,
            end=clip.source_end,
        )
        for clip in render_plan.clips
    ]
    motion_count = 0
    flash_count = 0

    # B-roll xfade transitions: each clip on the "from" side of an eligible
    # boundary gets `transition.duration_sec` of extra tail so the xfade
    # overlap is "free" and total duration stays in sync with the voiceover.
    transitions_on = _broll_transitions_enabled(cfg)
    eligible = (
        [_is_transition_eligible(clips[j], clips[j + 1], cfg) for j in range(len(clips) - 1)]
        if transitions_on
        else []
    )
    transition_duration = cfg.transition.duration_sec

    for i, (plan_clip, clip) in enumerate(zip(render_plan.clips, clips)):
        src = Path(clip.file)
        trimmed = out_dir / f"broll_trim_{i:02d}.mp4"
        norm = out_dir / f"broll_norm_{i:02d}.mp4"

        extend = transition_duration if (transitions_on and i < len(clips) - 1 and eligible[i]) else 0.0
        # Pull the overlap material from real source footage where available;
        # fall back to a frozen tail for the part the asset can't cover.
        src_extend = 0.0
        if extend > 0:
            src_extend = min(extend, max(0.0, _probe_duration(src) - clip.end))

        # trim (copy — без перекодирования)
        trim_cmd = [
            "ffmpeg", "-y",
            "-ss", str(clip.start), "-to", str(clip.end + src_extend),
            "-i", str(src),
            "-c", "copy",
            str(trimmed),
        ]
        _run(trim_cmd, f"trim broll {i}")

        # normalize resolution/fps/audio + приведение к BT.709 limited
        normalize_clip(
            trimmed,
            norm,
            cfg,
            motion_preset=_alternating_broll_motion_preset(i),
            motion_duration_sec=_clip_duration(clip),
            motion_strength=plan_clip.motion_strength or 1.0,
            look_profile=render_plan.look_profile,
        )
        motion_count += 1
        clip_out = norm
        deficit = extend - src_extend
        if deficit > 0.02:
            padded = out_dir / f"broll_norm_pad_{i:02d}.mp4"
            clip_out = _freeze_pad_tail(clip_out, padded, deficit, cfg)
        if _should_flash_broll_cut(i, cfg):
            flashed = out_dir / f"broll_norm_flash_{i:02d}.mp4"
            clip_out = _apply_flash_to_clip_start(
                norm,
                flashed,
                cfg,
                label=f"broll flash transition {i}",
            )
            flash_count += 1
        normalized_clips.append(clip_out)

    if transitions_on:
        concat_out = _concat_broll_with_xfade(normalized_clips, eligible, out_dir, cfg)
    else:
        concat_out = _concat_broll_video(normalized_clips, out_dir, cfg)
    if motion_count:
        console.log(f"[green]✓[/green] Applied motion to {motion_count} b-roll clip(s)")
    if flash_count:
        console.log(f"[green]✓[/green] Applied flash to {flash_count} b-roll cut(s)")

    # ОДИН проход: mix voiceover + burn subtitles за один энкод
    # (раньше было 2 прохода: mix → subs. Экономим один generation-loss.)
    tmp_ass = _copy_ass_to_temp(subtitles_path, "broll")
    vo_gain = cfg.audio.voiceover_gain_db
    sfx_events = render_plan.sfx_events
    section_out = out_dir / section_name
    audio_mix_inputs = ["[vo]"]
    filter_parts = [
        f"[0:v]{_subtitles_filter_value(tmp_ass, cfg)},"
        f"fps={cfg.video.fps},setpts=PTS-STARTPTS,setsar=1[vout]"
    ]
    filter_parts.append(
        f"[1:a]{_spoken_audio_filter(cfg, gain_db=vo_gain)}[vo]"
    )

    sample_inputs: list[Path] = []
    next_sample_input_index = 2  # 0 = concat video, 1 = voiceover
    for event in sfx_events:
        sample_idx: int | None = None
        if event.sample_path is not None:
            sample_path_resolved = resolve_project_path(event.sample_path)
            sample_inputs.append(sample_path_resolved)
            sample_idx = next_sample_input_index
            next_sample_input_index += 1
        filter_parts.append(
            _sfx_generator_chain(event, cfg, sample_input_index=sample_idx)
        )
        audio_mix_inputs.append(f"[sfx{event.sequence_index}]")

    filter_parts.append(
        f"{''.join(audio_mix_inputs)}amix=inputs={len(audio_mix_inputs)}:"
        f"duration=first:dropout_transition=0:normalize=0[aout]"
    )
    combined_cmd = [
        "ffmpeg", "-y",
        "-i", str(concat_out),
        "-i", str(voiceover_path),
    ]
    for sample_path in sample_inputs:
        combined_cmd.extend(["-i", str(sample_path)])
    combined_cmd.extend([
        "-filter_complex",
        ";".join(filter_parts),
        "-map", "[vout]", "-map", "[aout]",
        "-r", str(cfg.video.fps),
        "-c:v", cfg.video.codec,
        "-crf", str(cfg.video.crf_intermediate),
        "-x264-params", _X264_COLOR_PARAMS,
        *_COLOR_OUT_TAGS,
        "-c:a", "aac", "-ar", str(cfg.audio.sample_rate), "-ac", "2",
        "-shortest",
        str(section_out),
    ])
    _run(combined_cmd, "broll: mix voiceover + burn subtitles (single pass)")
    tmp_ass.unlink(missing_ok=True)
    if sfx_events:
        console.log(f"[green]✓[/green] Applied {len(sfx_events)} SFX event(s)")
    shutil.copy2(section_out, out_dir / "broll_section.mp4")
    return section_out


def add_background_music(
    video: Path,
    music: Path,
    out: Path,
    cfg: Config,
    *,
    start_sec: float = 0.0,
) -> Path:
    """Overlay looped, attenuated background music under existing audio with fade-out.
    Video stream copied — без перекодирования."""
    gain = cfg.audio.music_gain_db
    fade_in_sec = cfg.audio.music_fade_in_sec
    fade_sec = cfg.audio.music_fade_out_sec
    if cfg.audio.music_start_mode == "full_reel":
        start_sec = 0.0
    start_sec = max(0.0, start_sec)
    delay_ms = max(0, round(start_sec * 1000))

    # get video duration to schedule fade-out precisely
    video_dur = _probe_duration(video)
    fade_start = max(0.0, video_dur - fade_sec)

    music_filters = [
        f"volume={gain}dB",
    ]
    if delay_ms > 0:
        music_filters.append(f"adelay={delay_ms}|{delay_ms}")
    if fade_in_sec > 0:
        music_filters.append(
            f"afade=t=in:st={start_sec:.3f}:d={fade_in_sec:.3f}"
        )
    music_filters.append(f"afade=t=out:st={fade_start:.3f}:d={fade_sec}")

    if cfg.audio.music_ducking:
        # Голос (base) приглушает музыку через sidechaincompress и
        # отпускает её в паузах. base нужен дважды (триггер + финальный
        # микс), поэтому делим его asplit.
        duck = (
            f"sidechaincompress="
            f"threshold={cfg.audio.music_duck_threshold}:"
            f"ratio={cfg.audio.music_duck_ratio}:"
            f"attack={cfg.audio.music_duck_attack_ms}:"
            f"release={cfg.audio.music_duck_release_ms}"
        )
        filter_complex = (
            f"[0:a]apad=whole_dur={video_dur:.3f}[base];"
            f"[base]asplit=2[base_mix][base_key];"
            f"[1:a]{','.join(music_filters)}[music_pre];"
            f"[music_pre][base_key]{duck}[music];"
            f"[base_mix][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
        )
    else:
        filter_complex = (
            f"[0:a]apad=whole_dur={video_dur:.3f}[base];"
            f"[1:a]{','.join(music_filters)}[music];"
            f"[base][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
        )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video),
        "-stream_loop", "-1", "-i", str(music),
        "-filter_complex",
        filter_complex,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-ar", str(cfg.audio.sample_rate), "-ac", "2",
        "-shortest",
        str(out),
    ]
    _run(cmd, "add background music")
    return out


def _apply_flash_to_clip_start(src: Path, out: Path, cfg: Config, *, label: str) -> Path:
    transition = cfg.hook_montage.transition
    fade_out_sec = max(0.001, transition.duration_sec - transition.fade_in_sec)
    flash_source = (
        f"color=c={transition.color}:s={cfg.video.resolution}:"
        f"r={cfg.video.fps}:d={transition.duration_sec:.3f}"
    )
    filter_complex = (
        f"[1:v]format=rgba,colorchannelmixer=aa={transition.opacity:.3f},"
        f"fade=t=in:st=0:d={transition.fade_in_sec:.3f}:alpha=1,"
        f"fade=t=out:st={transition.fade_in_sec:.3f}:d={fade_out_sec:.3f}:alpha=1[flash];"
        f"[0:v][flash]overlay=x=0:y=0:eof_action=pass,"
        f"fps={cfg.video.fps},setpts=PTS-STARTPTS,setsar=1[vout]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-f", "lavfi", "-i", flash_source,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "0:a?",
        "-c:v", cfg.video.codec,
        "-r", str(cfg.video.fps),
        "-crf", str(cfg.video.crf_intermediate),
        "-x264-params", _X264_COLOR_PARAMS,
        *_COLOR_OUT_TAGS,
        "-c:a", "copy",
        str(out),
    ]
    _run(cmd, label)
    return out


def _apply_flash_to_clip_end(src: Path, out: Path, cfg: Config, *, label: str) -> Path:
    transition = cfg.hook_montage.transition
    duration = _probe_duration(src)
    flash_start = max(0.0, duration - transition.duration_sec)
    fade_out_start = flash_start + transition.fade_in_sec
    fade_out_sec = max(0.001, transition.duration_sec - transition.fade_in_sec)
    flash_source = (
        f"color=c={transition.color}:s={cfg.video.resolution}:"
        f"r={cfg.video.fps}:d={duration:.3f}"
    )
    filter_complex = (
        f"[1:v]format=rgba,colorchannelmixer=aa={transition.opacity:.3f},"
        f"fade=t=in:st={flash_start:.3f}:d={transition.fade_in_sec:.3f}:alpha=1,"
        f"fade=t=out:st={fade_out_start:.3f}:d={fade_out_sec:.3f}:alpha=1[flash];"
        f"[0:v][flash]overlay=x=0:y=0:eof_action=pass,"
        f"fps={cfg.video.fps},setpts=PTS-STARTPTS,setsar=1[vout]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-f", "lavfi", "-i", flash_source,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "0:a?",
        "-c:v", cfg.video.codec,
        "-r", str(cfg.video.fps),
        "-crf", str(cfg.video.crf_intermediate),
        "-x264-params", _X264_COLOR_PARAMS,
        *_COLOR_OUT_TAGS,
        "-c:a", "copy",
        str(out),
    ]
    _run(cmd, label)
    return out


def _apply_hook_flash_to_hook_tail(src: Path, out: Path, cfg: Config) -> Path:
    return _apply_flash_to_clip_end(src, out, cfg, label="hook flash transition")


def _concat_copy(parts: list[Path], concat_list: Path, out: Path, label: str) -> bool:
    """Try to concat via stream copy (no re-encode). Returns True on success."""
    concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in parts))
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy",
        str(out),
    ]
    cmd_str = " ".join(cmd)
    console.log(f"[dim]$ {cmd_str}[/dim]")
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - t0
    if result.returncode != 0:
        console.log(
            f"[yellow]![/yellow] {label} copy failed ({elapsed:.1f}s) — fallback to re-encode"
        )
        return False
    expected_duration = sum(_probe_duration(part) for part in parts)
    actual_duration = _probe_duration(out)
    tolerance = max(0.25, expected_duration * 0.02)
    if abs(actual_duration - expected_duration) > tolerance:
        console.log(
            f"[yellow]![/yellow] {label} copy produced timeline drift "
            f"({actual_duration:.3f}s vs {expected_duration:.3f}s) — fallback to re-encode"
        )
        return False
    console.log(f"[green]✓[/green] {label} (stream copy, {elapsed:.1f}s, NO re-encode)")
    return True


def _concat_reencode(parts: list[Path], out: Path, cfg: Config, label: str) -> Path:
    cmd = ["ffmpeg", "-y"]
    for part in parts:
        cmd.extend(["-i", str(part)])

    chains: list[str] = []
    labels: list[str] = []
    for index in range(len(parts)):
        video_label = f"v{index}"
        audio_label = f"a{index}"
        labels.append(f"[{video_label}][{audio_label}]")
        chains.append(
            f"[{index}:v]fps={cfg.video.fps},setpts=PTS-STARTPTS,setsar=1[{video_label}]"
        )
        chains.append(
            f"[{index}:a]aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS[{audio_label}]"
        )
    chains.append(
        f"{''.join(labels)}concat=n={len(parts)}:v=1:a=1[vout][aout]"
    )

    cmd.extend([
        "-filter_complex", ";".join(chains),
        "-map", "[vout]", "-map", "[aout]",
        "-r", str(cfg.video.fps),
        "-c:v", cfg.video.codec,
        "-crf", str(cfg.video.crf),
        "-x264-params", _X264_COLOR_PARAMS,
        *_COLOR_OUT_TAGS,
        "-c:a", "aac",
        "-ar", str(cfg.audio.sample_rate),
        "-ac", "2",
        str(out),
    ])
    _run(cmd, label)
    return out


def concat_final(
    hook: Path,
    broll_section: Path,
    cta: Path,
    out_dir: Path,
    cfg: Config,
    hook_ass: Path | None = None,
    cta_ass: Path | None = None,
    cta_transcript: Transcript | None = None,
    music_path: Path | None = None,
    broll_section_music: Path | None = None,
    look_profile: str | None = None,
    music_start_sec: float = 0.0,
) -> Path:
    # normalize hook and cta to same spec (BT.709 limited), burn asset-specific subs
    hook_norm = out_dir / "hook_norm.mp4"
    cta_norm = out_dir / "cta_norm.mp4"
    hook_lead_trim_sec = max(
        _probe_leading_black_duration(hook),
        _probe_exposure_ramp_duration(hook),
    )
    hook_duration_limit_sec = max(0.001, _probe_duration(hook) - hook_lead_trim_sec)
    if hook_lead_trim_sec > 0:
        console.log(
            f"[yellow]→[/yellow] Trimming {hook_lead_trim_sec:.3f}s leading frames from hook (black / exposure ramp)"
        )
    normalize_clip(
        hook,
        hook_norm,
        cfg,
        burn_ass=hook_ass,
        look_profile=look_profile,
        lead_trim_sec=hook_lead_trim_sec,
        duration_limit_sec=hook_duration_limit_sec,
    )
    _normalize_cta_clip(
        cta,
        cta_norm,
        cfg,
        transcript=cta_transcript,
        burn_ass=cta_ass,
        look_profile=look_profile,
    )

    hook_for_concat = hook_norm
    if cfg.hook_montage.enabled and cfg.hook_montage.transition.enabled:
        hook_for_concat = _apply_hook_flash_to_hook_tail(
            hook_norm,
            out_dir / "hook_norm_hook_flash.mp4",
            cfg,
        )

    parts = [hook_for_concat, broll_section, cta_norm]
    concat_list = out_dir / "final_concat.txt"

    # Все три куска нормализованы одним кодером с одинаковыми параметрами,
    # значит concat demuxer + stream copy должен сработать (0 перекодирований).
    concat_out = out_dir / "final_clean.mp4"
    if not _concat_copy(parts, concat_list, concat_out, "final concat"):
        _concat_reencode(parts, concat_out, cfg, "final concat (re-encode fallback)")

    shutil.copy2(concat_out, out_dir / "final_nomusic.mp4")
    if music_path:
        music_concat_src = concat_out
        if broll_section_music is not None:
            broll_music_for_concat = broll_section_music
            music_parts = [hook_for_concat, broll_music_for_concat, cta_norm]
            music_concat_list = out_dir / "final_music_concat.txt"
            music_concat_src = out_dir / "final_music_base.mp4"
            if not _concat_copy(music_parts, music_concat_list, music_concat_src, "final music concat"):
                _concat_reencode(
                    music_parts,
                    music_concat_src,
                    cfg,
                    "final music concat (re-encode fallback)",
                )
        final_out = out_dir / "final_music.mp4"
        add_background_music(
            music_concat_src,
            music_path,
            final_out,
            cfg,
            start_sec=music_start_sec,
        )
        shutil.copy2(final_out, out_dir / "final.mp4")
    else:
        final_out = concat_out
        shutil.copy2(final_out, out_dir / "final.mp4")
    console.log(f"[bold green]✓ DONE:[/bold green] {final_out}")
    return final_out
