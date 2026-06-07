from __future__ import annotations
import hashlib
import json
import re
import subprocess
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

import stable_whisper
from rich.console import Console

from .schemas import Transcript, Word
from .tts_text import prepare_subtitle_text, prepare_tts_text

_TOKEN_RE = re.compile(r"[^\s]+", re.UNICODE)
_NORM_RE = re.compile(r"[^\w]", re.UNICODE)
_SSML_RE = re.compile(r"<[^>]+>")
_ALIGN_CACHE_VERSION = "v3_loudnorm_free_timing_correction"
_FREE_TIMING_MIN_TOKEN_SIMILARITY = 0.72


def _strip_ssml(text: str) -> str:
    """Убирает SSML-теги вида <break time=\"0.5s\"/>, схлопывает пробелы."""
    return re.sub(r"\s+", " ", _SSML_RE.sub(" ", text)).strip()


def _cache_key(parts: list[str]) -> str:
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:12]


def _audio_signature(audio_path: Path) -> str:
    stat = audio_path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _norm(s: str) -> str:
    return _NORM_RE.sub("", s).lower()


def _meaningful_tokens(text: str) -> list[str]:
    """Tokens that survive alignment normalization.

    Punctuation-only tokens such as a standalone em dash are skipped because
    stable-ts has no word timing to attach to them, and they should not break
    spoken→display token projection.
    """
    return [token for token in _TOKEN_RE.findall(text) if _norm(token)]


def _retokenize_to_source(words: list[Word], known_text: str) -> list[Word]:
    """Re-split/merge aligned words so boundaries match source text tokens.

    stable-ts может склеивать или разбивать слова не так, как в оригинале
    (напр. "какими восьми" → "какимосьми", "эйчар" → "чар"). Прогоняем
    aligned-выход по токенам источника: если совпадает — берём как есть;
    если нет — мержим/режем интервалы пропорционально длине символов.
    """
    src_tokens = _meaningful_tokens(known_text)
    if not src_tokens or not words:
        return words

    aligned_concat = "".join(_norm(w.word) for w in words)
    src_concat = "".join(_norm(t) for t in src_tokens)
    if aligned_concat != src_concat:
        console.log(
            f"[yellow]![/yellow] alignment chars ({len(aligned_concat)}) != "
            f"source chars ({len(src_concat)}); skipping retokenize"
        )
        return words

    # build (start,end) per char from aligned words
    char_times: list[tuple[float, float]] = []
    for w in words:
        n = len(_norm(w.word))
        if n == 0:
            continue
        step = (w.end - w.start) / n
        for i in range(n):
            char_times.append((w.start + step * i, w.start + step * (i + 1)))

    out: list[Word] = []
    pos = 0
    for tok in src_tokens:
        n = len(_norm(tok))
        if n == 0:
            continue
        chunk = char_times[pos:pos + n]
        pos += n
        if not chunk:
            continue
        out.append(Word(word=tok, start=chunk[0][0], end=chunk[-1][1]))

    fixed = sum(1 for a, b in zip(words, out) if a.word.strip() != b.word.strip())
    if fixed or len(out) != len(words):
        console.log(
            f"[green]✓[/green] retokenized alignment to source: "
            f"{len(words)} → {len(out)} words"
        )
    return out


def _project_words_to_display(
    words: list[Word],
    display_text: str,
) -> list[Word]:
    """Swap spoken token texts back to display tokens while preserving timings.

    Kept as a timing-safe projection step for display text that differs from
    the aligned text, while the default pipeline now leaves authored wording
    unchanged.
    """
    display_clean = _strip_ssml(display_text)
    display_tokens = _meaningful_tokens(display_clean)
    spoken_display_tokens = _meaningful_tokens(prepare_tts_text(display_clean))

    if not words or not display_tokens:
        return words

    if len(words) != len(display_tokens) or len(words) != len(spoken_display_tokens):
        console.log(
            f"[yellow]![/yellow] display token count mismatch "
            f"({len(words)} spoken / {len(display_tokens)} display / "
            f"{len(spoken_display_tokens)} display-spoken); keeping spoken tokens"
        )
        return words

    aligned_concat = "".join(_norm(w.word) for w in words)
    spoken_display_concat = "".join(_norm(t) for t in spoken_display_tokens)
    if aligned_concat != spoken_display_concat:
        console.log(
            f"[yellow]![/yellow] display/spoken chars mismatch "
            f"({len(aligned_concat)} != {len(spoken_display_concat)}); "
            f"keeping spoken tokens"
        )
        return words

    return [
        Word(word=display_tok, start=word.start, end=word.end)
        for word, display_tok in zip(words, display_tokens)
    ]


def _token_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _norm(left), _norm(right)).ratio()


def _distribute_word_timings(source_tokens: list[str], recognized: list[Word]) -> list[Word]:
    start = recognized[0].start
    end = recognized[-1].end
    weights = [max(1, len(_norm(token))) for token in source_tokens]
    total = sum(weights)
    cursor = start
    out: list[Word] = []
    for index, (token, weight) in enumerate(zip(source_tokens, weights)):
        next_cursor = end if index == len(source_tokens) - 1 else start + (end - start) * sum(weights[: index + 1]) / total
        out.append(Word(word=token, start=cursor, end=next_cursor))
        cursor = next_cursor
    return out


def _fill_missing_timing(
    token: str,
    previous: Word | None,
    next_word: Word | None,
) -> Word:
    min_duration = 0.04
    if previous is not None and next_word is not None:
        start = previous.end
        end = next_word.start
        if end - start >= min_duration:
            return Word(word=token, start=start, end=end)
        midpoint = (start + end) / 2
        return Word(word=token, start=max(0.0, midpoint - min_duration / 2), end=midpoint + min_duration / 2)
    if previous is not None:
        return Word(word=token, start=previous.end, end=previous.end + min_duration)
    if next_word is not None:
        return Word(word=token, start=max(0.0, next_word.start - min_duration), end=next_word.start)
    return Word(word=token, start=0.0, end=min_duration)


def _project_free_timings_to_source(
    recognized_words: list[Word],
    source_text: str,
) -> list[Word] | None:
    """Use free transcription timings while keeping source token text.

    stable-ts forced alignment can occasionally assign locally bad timings to
    short words even when the text is known. A free transcription of the same
    TTS audio often has much better word boundaries. When token counts and
    order are close enough, we keep the authored tokens and borrow those real
    timings.
    """
    source_tokens = _meaningful_tokens(source_text)
    recognized_tokens = [word for word in recognized_words if _norm(word.word)]
    if not source_tokens or not recognized_tokens:
        return None

    if len(source_tokens) != len(recognized_tokens):
        source_norms = [_norm(token) for token in source_tokens]
        recognized_norms = [_norm(word.word) for word in recognized_tokens]
        matcher = SequenceMatcher(None, source_norms, recognized_norms)
        if matcher.ratio() < _FREE_TIMING_MIN_TOKEN_SIMILARITY:
            console.log(
                f"[yellow]![/yellow] free timing correction skipped: "
                f"token count mismatch ({len(source_tokens)} source / "
                f"{len(recognized_tokens)} recognized), similarity {matcher.ratio():.2f}"
            )
            return None

        corrected: list[Word | None] = [None] * len(source_tokens)
        for tag, source_start, source_end, recognized_start, recognized_end in matcher.get_opcodes():
            if tag == "equal":
                for source_index, recognized_index in zip(
                    range(source_start, source_end),
                    range(recognized_start, recognized_end),
                ):
                    recognized = recognized_tokens[recognized_index]
                    corrected[source_index] = Word(
                        word=source_tokens[source_index],
                        start=recognized.start,
                        end=recognized.end,
                    )
            elif tag == "replace" and recognized_start < recognized_end:
                distributed = _distribute_word_timings(
                    source_tokens[source_start:source_end],
                    recognized_tokens[recognized_start:recognized_end],
                )
                for offset, word in enumerate(distributed):
                    corrected[source_start + offset] = word

        for index, word in enumerate(corrected):
            if word is not None:
                continue
            previous = next((item for item in reversed(corrected[:index]) if item is not None), None)
            next_word = next((item for item in corrected[index + 1 :] if item is not None), None)
            corrected[index] = _fill_missing_timing(source_tokens[index], previous, next_word)

        out = [word for word in corrected if word is not None]
        console.log(
            f"[green]✓[/green] corrected alignment timings from fuzzy free transcription "
            f"({len(source_tokens)} source / {len(recognized_tokens)} recognized, "
            f"similarity={matcher.ratio():.2f})"
        )
        return out

    similarities = [
        _token_similarity(source_token, recognized.word)
        for source_token, recognized in zip(source_tokens, recognized_tokens)
    ]
    average_similarity = sum(similarities) / len(similarities)
    if average_similarity < _FREE_TIMING_MIN_TOKEN_SIMILARITY:
        console.log(
            f"[yellow]![/yellow] free timing correction skipped: "
            f"token similarity {average_similarity:.2f}"
        )
        return None

    corrected = [
        Word(
            word=source_token,
            start=recognized.start,
            end=recognized.end,
        )
        for source_token, recognized in zip(source_tokens, recognized_tokens)
    ]
    console.log(
        f"[green]✓[/green] corrected alignment timings from free transcription "
        f"({len(corrected)} words, similarity={average_similarity:.2f})"
    )
    return corrected


def _prepare_timing_audio(audio_path: Path, out_path: Path) -> Path:
    """Match the voiceover audio shape used in the rendered b-roll section."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(audio_path),
        "-af",
        (
            "loudnorm=I=-16.0:TP=-1.5:LRA=11.0,"
            "volume=3.0dB,"
            "aformat=sample_rates=48000:channel_layouts=stereo"
        ),
        "-c:a", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    return out_path

console = Console()

_model: object | None = None
_model_size: str = ""


def _get_model(model_size: str):
    """Load stable-ts model once (uses faster-whisper backend)."""
    global _model, _model_size
    if _model is None or _model_size != model_size:
        console.log(f"[blue]→[/blue] Loading Whisper model '{model_size}'...")
        _model = stable_whisper.load_faster_whisper(model_size, device="cpu", compute_type="int8")
        _model_size = model_size
    return _model


def _result_to_transcript(result) -> Transcript:
    words: list[Word] = []
    for segment in result.segments:
        for w in segment.words:
            words.append(Word(word=w.word, start=float(w.start), end=float(w.end)))
    full_text = " ".join(w.word.strip() for w in words)
    duration = words[-1].end if words else 0.0
    return Transcript(words=words, full_text=full_text, duration=duration)


def align_to_text(
    audio_path: Path,
    known_text: str,
    out_path: Path,
    language: str = "ru",
    model_size: str = "base",
    display_text: str | None = None,
) -> Transcript:
    """Forced alignment: use the exact known text, only infer word timestamps.

    This gives 0 transcription errors because we use the source text verbatim —
    only word-level timestamps are computed from the audio.
    """
    clean_text = _strip_ssml(known_text)
    clean_display_text = (
        _strip_ssml(prepare_subtitle_text(display_text))
        if display_text is not None
        else clean_text
    )
    hash_path = out_path.with_suffix(out_path.suffix + ".hash")
    current_hash = _cache_key([
        _ALIGN_CACHE_VERSION,
        _audio_signature(audio_path),
        clean_text,
        clean_display_text,
        language,
        model_size,
    ])
    if out_path.exists() and hash_path.exists() and hash_path.read_text().strip() == current_hash:
        console.log(f"[yellow]→[/yellow] Alignment cache hit, loading from disk")
        return Transcript(**json.loads(out_path.read_text()))

    model = _get_model(model_size)
    console.log(f"[blue]→[/blue] Aligning known text to {audio_path.name}...")

    result = model.align(str(audio_path), clean_text, language=language)
    transcript = _result_to_transcript(result)
    transcript.words = _retokenize_to_source(transcript.words, clean_text)
    with tempfile.TemporaryDirectory(prefix="alignment_timing_") as tmp_dir:
        timing_audio = _prepare_timing_audio(
            audio_path,
            Path(tmp_dir) / "timing_audio.wav",
        )
        free_result = model.transcribe(
            str(timing_audio),
            language=language,
            word_timestamps=True,
        )
    free_transcript = _result_to_transcript(free_result)
    corrected_words = _project_free_timings_to_source(free_transcript.words, clean_text)
    if corrected_words is not None:
        transcript.words = corrected_words
    if display_text is not None:
        transcript.words = _project_words_to_display(transcript.words, clean_display_text)
    transcript.full_text = " ".join(w.word.strip() for w in transcript.words)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(transcript.model_dump_json(indent=2))
    hash_path.write_text(current_hash)
    console.log(
        f"[green]✓[/green] Alignment saved: {out_path} "
        f"({len(transcript.words)} words, {transcript.duration:.1f}s)"
    )
    return transcript


def transcribe(
    audio_path: Path,
    out_path: Path,
    language: str = "ru",
    model_size: str = "medium",
) -> Transcript:
    """Free transcription (no known text). Used for hook/CTA audio."""
    if out_path.exists():
        console.log(f"[yellow]→[/yellow] Transcript cache hit, loading from disk")
        return Transcript(**json.loads(out_path.read_text()))

    model = _get_model(model_size)
    console.log(f"[blue]→[/blue] Transcribing {audio_path.name}...")

    result = model.transcribe(str(audio_path), language=language, word_timestamps=True)
    transcript = _result_to_transcript(result)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(transcript.model_dump_json(indent=2))
    console.log(
        f"[green]✓[/green] Transcript saved: {out_path} "
        f"({len(transcript.words)} words, {transcript.duration:.1f}s)"
    )
    return transcript
