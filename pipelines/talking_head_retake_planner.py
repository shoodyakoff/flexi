from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from difflib import SequenceMatcher
from typing import Any, Literal

from src.schemas import Transcript, Word


RetakeMode = Literal["off", "safe", "smart", "aggressive"]

_TOKEN_RE = re.compile(r"[\wёЁ]+", re.UNICODE)
_FILLER_TOKENS = {
    "а",
    "э",
    "ээ",
    "эм",
    "мм",
    "м",
    "ну",
    "вот",
    "типа",
    "какбы",
    "кхм",
    "гм",
    "гхм",
    "хм",
    "кашель",
    "кашляю",
}
_COUGH_TOKENS = {"кхм", "гм", "гхм", "хм", "кашель", "кашляю"}
_MIN_SPEECH_FRAGMENT_SEC = 0.35
_REPAIR_SPAN_GAP_SEC = 2.5
_REPAIR_COMPARE_GAP_SEC = 8.0
_REPAIR_MAX_SPAN_ATTEMPTS = 3
_REPAIR_MAX_SPAN_SEC = 16.0
_ANCHOR_STOP_TOKENS = {
    "а",
    "и",
    "но",
    "в",
    "во",
    "на",
    "с",
    "со",
    "у",
    "о",
    "об",
    "к",
    "ко",
    "за",
    "под",
    "то",
    "же",
    "ни",
}
_CONNECTIVE_TOKENS = _ANCHOR_STOP_TOKENS | {
    "что",
    "как",
    "если",
    "поэтому",
    "вообще",
    "это",
}
_CONTRAST_STARTS = (
    (("с", "одной", "стороны"), ("с", "другой", "стороны")),
    (("во", "первых"), ("во", "вторых")),
    (("во", "первых"), ("во", "вторую")),
)
_CORRECTION_MARKER_TOKENS = {"точнее", "вернее"}
_BOUNDARY_GENERIC_TOKENS = _CONNECTIVE_TOKENS | {
    "я",
    "ты",
    "вы",
    "мы",
    "он",
    "она",
    "оно",
    "они",
    "мне",
    "меня",
    "тебе",
    "тебя",
    "нам",
    "нас",
    "вам",
    "вас",
    "себя",
}


@dataclass(frozen=True)
class TimedRange:
    source_index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class PhraseAttempt:
    index: int
    source_index: int
    start: float
    end: float
    text: str
    tokens: list[str]
    score: float
    filler_count: int


@dataclass(frozen=True)
class RetakeDecision:
    status: str
    reason: str
    confidence: float
    attempts: list[PhraseAttempt]
    chosen_ranges: list[TimedRange]
    removed_ranges: list[TimedRange]
    splice: dict[str, Any] | None = None
    group_type: str = "exact_retake"
    features: dict[str, Any] | None = None
    kept_attempt_indexes: list[int] | None = None
    removed_attempt_indexes: list[int] | None = None
    transcript_source: str = "primary"


@dataclass(frozen=True)
class RepairSpan:
    attempts: list[PhraseAttempt]
    source_index: int
    start: float
    end: float
    text: str
    tokens: list[str]

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def attempt_indexes(self) -> list[int]:
        return [attempt.index for attempt in self.attempts]


@dataclass(frozen=True)
class RetakePlan:
    mode: RetakeMode
    confidence_threshold: float
    source_index: int
    speech_segments: list[TimedRange]
    decisions: list[RetakeDecision]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "confidence_threshold": self.confidence_threshold,
            "source_index": self.source_index,
            "speech_segments": [asdict(segment) for segment in self.speech_segments],
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
                for decision in self.decisions
            ],
            "summary": {
                "applied": sum(1 for decision in self.decisions if decision.status == "applied"),
                "needs_review": sum(1 for decision in self.decisions if decision.status == "needs_review"),
                "ignored": sum(1 for decision in self.decisions if decision.status == "ignored"),
            },
        }


def normalize_token(text: str) -> str:
    return "".join(_TOKEN_RE.findall(text.lower().replace("ё", "е")))


def meaningful_tokens(words: list[Word]) -> list[str]:
    return [
        token
        for word in words
        for token in [normalize_token(word.word)]
        if token and token not in _FILLER_TOKENS
    ]


def _source_index(segment: Any, fallback: int) -> int:
    if isinstance(segment, dict):
        return int(segment.get("source_index", fallback))
    return int(getattr(segment, "source_index", fallback))


def _start(segment: Any) -> float:
    if isinstance(segment, dict):
        return float(segment["start"])
    return float(getattr(segment, "start"))


def _end(segment: Any) -> float:
    if isinstance(segment, dict):
        return float(segment["end"])
    return float(getattr(segment, "end"))


def phrase_attempts_from_transcript(
    transcript: Transcript,
    *,
    source_index: int,
    phrase_gap_sec: float = 0.55,
) -> list[PhraseAttempt]:
    if not transcript.words:
        return []

    sorted_words = sorted(transcript.words, key=lambda word: word.start)
    groups: list[list[Word]] = [[sorted_words[0]]]
    for previous, word in zip(sorted_words, sorted_words[1:]):
        if word.start - previous.end >= phrase_gap_sec:
            groups.append([word])
        else:
            groups[-1].append(word)

    attempts: list[PhraseAttempt] = []
    for index, words in enumerate(groups):
        tokens = meaningful_tokens(words)
        if not tokens:
            continue
        text = " ".join(word.word.strip() for word in words).strip()
        filler_count = sum(1 for word in words if normalize_token(word.word) in _FILLER_TOKENS)
        score = _attempt_score(
            index=index,
            tokens=tokens,
            raw_words=words,
            filler_count=filler_count,
        )
        attempts.append(
            PhraseAttempt(
                index=index,
                source_index=source_index,
                start=round(words[0].start, 3),
                end=round(words[-1].end, 3),
                text=text,
                tokens=tokens,
                score=round(score, 3),
                filler_count=filler_count,
            )
        )
    return attempts


def _attempt_score(
    *,
    index: int,
    tokens: list[str],
    raw_words: list[Word],
    filler_count: int,
) -> float:
    duration = max(0.001, raw_words[-1].end - raw_words[0].start)
    token_count = len(tokens)
    score = token_count * 0.28 + min(duration, 8.0) * 0.12 + index * 0.18
    score -= filler_count * 0.75
    score -= sum(1 for token in tokens if token in _COUGH_TOKENS) * 1.4
    if token_count <= 2:
        score -= 1.25
    if duration < 0.7:
        score -= 0.8
    if normalize_token(raw_words[-1].word) in _FILLER_TOKENS:
        score -= 0.6
    return score


def _token_similarity(left: list[str], right: list[str]) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _prefix_similarity(left: list[str], right: list[str]) -> float:
    length = min(len(left), len(right), 5)
    if length == 0:
        return 0.0
    matches = sum(1 for index in range(length) if left[index] == right[index])
    return matches / length


def _longest_common_ngram(left: list[str], right: list[str], max_len: int = 5) -> int:
    longest = 0
    for length in range(2, min(len(left), len(right), max_len) + 1):
        left_ngrams = {tuple(left[index : index + length]) for index in range(len(left) - length + 1)}
        if any(tuple(right[index : index + length]) in left_ngrams for index in range(len(right) - length + 1)):
            longest = length
    return longest


def _find_subsequence(tokens: list[str], needle: list[str]) -> int | None:
    if not needle or len(needle) > len(tokens):
        return None
    for index in range(len(tokens) - len(needle) + 1):
        if tokens[index : index + len(needle)] == needle:
            return index
    return None


def _common_prefix_len(left: list[str], right: list[str]) -> int:
    count = 0
    for left_token, right_token in zip(left, right):
        if left_token != right_token:
            break
        count += 1
    return count


def _starts_with(tokens: list[str], prefix: tuple[str, ...]) -> bool:
    return tuple(tokens[: len(prefix)]) == prefix


def _has_contrast_start(left: list[str], right: list[str]) -> bool:
    for first, second in _CONTRAST_STARTS:
        if _starts_with(left, first) and _starts_with(right, second):
            return True
        if _starts_with(left, second) and _starts_with(right, first):
            return True
    return False


def _ngram_counts(tokens: list[str], n: int) -> dict[tuple[str, ...], int]:
    counts: dict[tuple[str, ...], int] = {}
    if len(tokens) < n:
        return counts
    for index in range(len(tokens) - n + 1):
        ngram = tuple(tokens[index : index + n])
        if all(token in _CONNECTIVE_TOKENS for token in ngram):
            continue
        counts[ngram] = counts.get(ngram, 0) + 1
    return counts


def _repeated_ngram_count(tokens: list[str], *, min_n: int = 2, max_n: int = 4) -> int:
    repeated = 0
    for n in range(min_n, max_n + 1):
        repeated += sum(count - 1 for count in _ngram_counts(tokens, n).values() if count > 1)
    return repeated


def _first_repeated_ngram_start_index(tokens: list[str], *, min_n: int = 2, max_n: int = 4) -> int | None:
    best: tuple[int, int] | None = None
    for n in range(max_n, min_n - 1, -1):
        seen: dict[tuple[str, ...], int] = {}
        for index in range(len(tokens) - n + 1):
            ngram = tuple(tokens[index : index + n])
            if all(token in _CONNECTIVE_TOKENS for token in ngram):
                continue
            if ngram in seen:
                candidate = (seen[ngram], -n)
                if best is None or candidate < best:
                    best = candidate
                continue
            seen[ngram] = index
    return None if best is None else best[0]


def _longest_common_ngram_size(left: list[str], right: list[str], *, max_n: int = 4) -> int:
    for n in range(min(len(left), len(right), max_n), 1, -1):
        left_ngrams = set(_ngram_counts(left, n))
        if any(ngram in left_ngrams for ngram in _ngram_counts(right, n)):
            return n
    return 0


def _anchor_tokens(tokens: list[str]) -> list[str]:
    return [token for token in tokens if token and token not in _ANCHOR_STOP_TOKENS and token not in _FILLER_TOKENS]


def _suspicious_asr_tokens(tokens: list[str]) -> list[str]:
    # Weak generic signal only: long unusual words often appear in bad takes or rough ASR.
    return [token for token in tokens if len(token) >= 14 and not token.isdigit()]


def _connective_ratio(tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    return sum(1 for token in tokens if token in _CONNECTIVE_TOKENS) / len(tokens)


def _failure_score_for_tokens(tokens: list[str], *, duration: float, attempt_count: int) -> float:
    repeated = _repeated_ngram_count(tokens)
    connective_ratio = _connective_ratio(tokens)
    suspicious_count = len(_suspicious_asr_tokens(tokens))
    score = repeated * 0.75 + suspicious_count * 0.22
    if duration >= 7.0 and attempt_count > 1:
        score += 0.45
    if duration >= 9.0:
        score += 0.25
    if connective_ratio >= 0.34:
        score += min(0.35, (connective_ratio - 0.34) * 1.2)
    return round(score, 3)


def _is_full_clean_contrast_pair(left: PhraseAttempt, right: PhraseAttempt) -> bool:
    if not _has_contrast_start(left.tokens, right.tokens):
        return False
    if len(left.tokens) < 5 or len(right.tokens) < 5:
        return False
    left_failure = _failure_score_for_tokens(left.tokens, duration=left.end - left.start, attempt_count=1)
    right_failure = _failure_score_for_tokens(right.tokens, duration=right.end - right.start, attempt_count=1)
    return left_failure < 0.5 and right_failure < 0.5


def _attempts_related(left: PhraseAttempt, right: PhraseAttempt, *, aggressive: bool) -> bool:
    max_gap = 22.0 if aggressive else 18.0
    if right.start - left.end > max_gap:
        return False
    if _is_full_clean_contrast_pair(left, right):
        return False
    if _prefix_similarity(left.tokens, right.tokens) >= (0.45 if aggressive else 0.5):
        return True
    if _token_similarity(left.tokens, right.tokens) >= (0.45 if aggressive else 0.52):
        return True
    restart_index = _find_subsequence(left.tokens, right.tokens[:2])
    if restart_index is None:
        return False
    return restart_index >= max(3, int(len(left.tokens) * 0.45))


def group_retake_attempts(attempts: list[PhraseAttempt], *, aggressive: bool = False) -> list[list[PhraseAttempt]]:
    groups: list[list[PhraseAttempt]] = []
    current: list[PhraseAttempt] = []
    for attempt in attempts:
        if not current:
            current = [attempt]
            continue
        if any(_attempts_related(previous, attempt, aggressive=aggressive) for previous in current):
            current.append(attempt)
            continue
        if len(current) > 1:
            groups.append(current)
        current = [attempt]
    if len(current) > 1:
        groups.append(current)
    return groups


def _repair_span_from_attempts(attempts: list[PhraseAttempt]) -> RepairSpan:
    return RepairSpan(
        attempts=attempts,
        source_index=attempts[0].source_index,
        start=round(attempts[0].start, 3),
        end=round(attempts[-1].end, 3),
        text=" ".join(attempt.text for attempt in attempts).strip(),
        tokens=[token for attempt in attempts for token in attempt.tokens],
    )


def _repair_spans_from_attempts(attempts: list[PhraseAttempt]) -> list[RepairSpan]:
    spans: list[RepairSpan] = []
    for start_index, first in enumerate(attempts):
        current = [first]
        spans.append(_repair_span_from_attempts(current))
        for attempt in attempts[start_index + 1 : start_index + _REPAIR_MAX_SPAN_ATTEMPTS]:
            if attempt.source_index != current[-1].source_index:
                break
            if attempt.start - current[-1].end > _REPAIR_SPAN_GAP_SEC:
                break
            candidate = [*current, attempt]
            if candidate[-1].end - candidate[0].start > _REPAIR_MAX_SPAN_SEC:
                break
            current = candidate
            spans.append(_repair_span_from_attempts(current))
    return [span for span in spans if len(span.tokens) >= 2]


def _span_failure_score(span: RepairSpan) -> float:
    return _failure_score_for_tokens(span.tokens, duration=span.duration, attempt_count=len(span.attempts))


def _span_token_start(span: RepairSpan, transcript: Transcript, token_index: int) -> float | None:
    words = [
        word
        for word in sorted(transcript.words, key=lambda item: item.start)
        if span.start - 0.001 <= word.start and word.end <= span.end + 0.001
    ]
    meaningful = [
        (normalize_token(word.word), word.start)
        for word in words
        if normalize_token(word.word) and normalize_token(word.word) not in _FILLER_TOKENS
    ]
    if token_index >= len(meaningful):
        return None
    return meaningful[token_index][1]


def _attempt_meaningful_words(attempt: PhraseAttempt, transcript: Transcript) -> list[Word]:
    return [
        word
        for word in sorted(transcript.words, key=lambda item: item.start)
        if attempt.start - 0.001 <= word.start
        and word.end <= attempt.end + 0.001
        and normalize_token(word.word)
        and normalize_token(word.word) not in _FILLER_TOKENS
    ]


def _attempt_token_start(attempt: PhraseAttempt, transcript: Transcript, token_index: int) -> float | None:
    words = _attempt_meaningful_words(attempt, transcript)
    if token_index >= len(words):
        return None
    return words[token_index].start


def _attempt_token_end(attempt: PhraseAttempt, transcript: Transcript, token_index: int) -> float | None:
    words = _attempt_meaningful_words(attempt, transcript)
    if token_index >= len(words):
        return None
    return words[token_index].end


def _word_token(word: Word) -> str:
    return normalize_token(word.word)


def _include_nearby_leading_fillers(attempt: PhraseAttempt, transcript: Transcript, token_start: float) -> float:
    start = token_start
    for word in reversed(
        [
            word
            for word in sorted(transcript.words, key=lambda item: item.start)
            if attempt.start - 0.001 <= word.start < start
        ]
    ):
        token = _word_token(word)
        if token not in _FILLER_TOKENS:
            break
        if start - word.end > 0.28:
            break
        start = word.start
    return round(start, 3)


def _local_phrase_restart_candidate(
    attempt: PhraseAttempt,
    *,
    transcript: Transcript,
    padding_sec: float,
) -> RetakeDecision | None:
    if len(attempt.tokens) < 6 or attempt.end - attempt.start > 12.0:
        return None

    best: tuple[int, int, int] | None = None
    max_n = min(4, len(attempt.tokens) // 2)
    for n in range(max_n, 1, -1):
        seen: dict[tuple[str, ...], int] = {}
        for index in range(len(attempt.tokens) - n + 1):
            ngram = tuple(attempt.tokens[index : index + n])
            if all(token in _CONNECTIVE_TOKENS for token in ngram):
                continue
            if ngram not in seen:
                seen[ngram] = index
                continue
            first_index = seen[ngram]
            if index - first_index < n:
                continue
            if first_index > 5:
                continue
            best = (n, first_index, index)
            break
        if best is not None:
            break

    if best is None:
        return None

    n, first_index, second_index = best
    first_start = _attempt_token_start(attempt, transcript, first_index)
    second_start = _attempt_token_start(attempt, transcript, second_index)
    second_overlap_end = _attempt_token_end(attempt, transcript, second_index + n - 1)
    if first_start is None or second_start is None or second_overlap_end is None:
        return None

    failed_duration = second_start - first_start
    if failed_duration <= 0 or failed_duration > 3.0:
        return None

    first_fragment = attempt.tokens[first_index:second_index]
    kept_suffix = attempt.tokens[second_index:]
    suffix_gain = len(_anchor_tokens(kept_suffix[n:]))
    first_fragment_failure = _failure_score_for_tokens(
        first_fragment,
        duration=failed_duration,
        attempt_count=1,
    )
    if len(first_fragment) < n + 1:
        first_fragment_failure += 0.25
    if suffix_gain < 2 and first_fragment_failure < 0.45:
        return None

    remove_start = _include_nearby_leading_fillers(attempt, transcript, first_start)
    keep_start = _include_nearby_leading_fillers(attempt, transcript, second_start)
    removed_range = TimedRange(
        source_index=attempt.source_index,
        start=round(max(0.0, remove_start - padding_sec), 3),
        end=round(max(remove_start, keep_start - padding_sec), 3),
    )
    chosen_range = TimedRange(
        source_index=attempt.source_index,
        start=round(max(0.0, keep_start - padding_sec), 3),
        end=round(min(transcript.duration, attempt.end + padding_sec), 3),
    )
    if removed_range.duration < _MIN_SPEECH_FRAGMENT_SEC:
        return None

    confidence = 0.78
    confidence += min(0.07, n * 0.02)
    confidence += 0.04 if failed_duration <= 1.8 else 0.0
    confidence += min(0.06, suffix_gain * 0.015)
    confidence += min(0.05, first_fragment_failure * 0.05)
    confidence = round(min(0.95, confidence), 3)
    return RetakeDecision(
        status="applied",
        reason="local_phrase_restart",
        confidence=confidence,
        attempts=[attempt],
        chosen_ranges=[chosen_range],
        removed_ranges=[removed_range],
        splice={
            "attempt_index": attempt.index,
            "repeated_tokens": attempt.tokens[first_index : first_index + n],
            "first_token_index": first_index,
            "second_token_index": second_index,
            "second_overlap_end_sec": round(second_overlap_end, 3),
        },
        group_type="local_phrase_restart",
        features={
            "failed_duration": round(failed_duration, 3),
            "suffix_anchor_gain": suffix_gain,
            "first_fragment_failure_score": round(first_fragment_failure, 3),
            "removed_text": " ".join(first_fragment),
            "kept_text": " ".join(kept_suffix),
        },
        kept_attempt_indexes=[attempt.index],
        removed_attempt_indexes=[attempt.index],
    )


def build_local_phrase_restart_decisions(
    attempts: list[PhraseAttempt],
    *,
    mode: RetakeMode,
    transcript: Transcript,
    padding_sec: float,
) -> list[RetakeDecision]:
    if mode not in {"smart", "aggressive"}:
        return []
    return [
        decision
        for attempt in attempts
        for decision in [_local_phrase_restart_candidate(attempt, transcript=transcript, padding_sec=padding_sec)]
        if decision is not None
    ]


def _is_correction_marker_attempt(attempt: PhraseAttempt) -> bool:
    return (
        1 <= len(attempt.tokens) <= 3
        and any(token in _CORRECTION_MARKER_TOKENS for token in attempt.tokens)
        and attempt.end - attempt.start <= 1.2
    )


def _correction_marker_rephrase_candidate(
    previous: PhraseAttempt,
    marker: PhraseAttempt,
    replacement: PhraseAttempt,
    *,
    transcript: Transcript,
    padding_sec: float,
) -> RetakeDecision | None:
    if previous.source_index != marker.source_index or marker.source_index != replacement.source_index:
        return None
    if marker.start - previous.end < 0 or marker.start - previous.end > 1.5:
        return None
    if replacement.start - marker.end < 0 or replacement.start - marker.end > 2.0:
        return None
    if len(previous.tokens) < 2 or len(previous.tokens) > 8:
        return None
    if len(replacement.tokens) < 4:
        return None
    if _has_contrast_start(previous.tokens, replacement.tokens):
        return None

    previous_anchors = {token for token in _anchor_tokens(previous.tokens) if token not in _BOUNDARY_GENERIC_TOKENS}
    replacement_anchors = {token for token in _anchor_tokens(replacement.tokens) if token not in _BOUNDARY_GENERIC_TOKENS}
    replacement_gain = len(replacement_anchors - previous_anchors)
    common_prefix = _common_prefix_len(previous.tokens, replacement.tokens)
    common_ngram = _longest_common_ngram_size(previous.tokens, replacement.tokens)
    if replacement_gain < 2 and common_prefix < 1 and common_ngram < 2:
        return None

    removed_range = TimedRange(
        source_index=previous.source_index,
        start=round(max(0.0, previous.start - padding_sec), 3),
        end=round(max(marker.end, replacement.start - padding_sec), 3),
    )
    chosen_range = TimedRange(
        source_index=replacement.source_index,
        start=round(max(0.0, replacement.start - padding_sec), 3),
        end=round(min(transcript.duration, replacement.end + padding_sec), 3),
    )
    if removed_range.duration < _MIN_SPEECH_FRAGMENT_SEC:
        return None

    confidence = 0.83
    confidence += min(0.06, replacement_gain * 0.012)
    confidence += min(0.04, common_prefix * 0.015)
    confidence += 0.03 if len(replacement.tokens) >= len(previous.tokens) + 2 else 0.0
    confidence = round(min(0.96, confidence), 3)
    return RetakeDecision(
        status="applied",
        reason="correction_marker_rephrase",
        confidence=confidence,
        attempts=[previous, marker, replacement],
        chosen_ranges=[chosen_range],
        removed_ranges=[removed_range],
        splice={
            "previous_attempt_index": previous.index,
            "marker_attempt_index": marker.index,
            "replacement_attempt_index": replacement.index,
            "marker_tokens": marker.tokens,
        },
        group_type="correction_marker_rephrase",
        features={
            "marker_tokens": marker.tokens,
            "previous_text": previous.text,
            "replacement_text": replacement.text,
            "replacement_anchor_gain": replacement_gain,
            "common_prefix_len": common_prefix,
            "common_ngram": common_ngram,
        },
        kept_attempt_indexes=[replacement.index],
        removed_attempt_indexes=[previous.index, marker.index],
    )


def build_correction_marker_rephrase_decisions(
    attempts: list[PhraseAttempt],
    *,
    mode: RetakeMode,
    transcript: Transcript,
    padding_sec: float,
) -> list[RetakeDecision]:
    if mode not in {"smart", "aggressive"}:
        return []
    decisions: list[RetakeDecision] = []
    for index, marker in enumerate(attempts):
        if index == 0 or index + 1 >= len(attempts):
            continue
        if not _is_correction_marker_attempt(marker):
            continue
        decision = _correction_marker_rephrase_candidate(
            attempts[index - 1],
            marker,
            attempts[index + 1],
            transcript=transcript,
            padding_sec=padding_sec,
        )
        if decision is not None:
            decisions.append(decision)
    return decisions


def _boundary_phrase_restart_candidate(
    early: PhraseAttempt,
    late: PhraseAttempt,
    *,
    mode: RetakeMode,
    threshold: float,
    padding_sec: float,
    source_durations: list[float],
) -> RetakeDecision | None:
    if mode not in {"smart", "aggressive"}:
        return None
    if early.source_index == late.source_index:
        return None
    early_duration = early.end - early.start
    late_duration = late.end - late.start
    if early_duration <= 0 or early_duration > 4.0 or late_duration < 0.8:
        return None
    if len(early.tokens) > 10 or len(late.tokens) < 4:
        return None

    common_prefix = _common_prefix_len(early.tokens, late.tokens)
    early_boundary_anchors = {
        token for token in _anchor_tokens(early.tokens) if token not in _BOUNDARY_GENERIC_TOKENS
    }
    late_boundary_anchors = {
        token for token in _anchor_tokens(late.tokens) if token not in _BOUNDARY_GENERIC_TOKENS
    }
    common_anchors = sorted(early_boundary_anchors & late_boundary_anchors)
    common_ngram = _longest_common_ngram_size(early.tokens, late.tokens)
    token_similarity = _token_similarity(early.tokens, late.tokens)
    late_anchor_gain = len(late_boundary_anchors - early_boundary_anchors)
    if common_prefix < 2 and len(common_anchors) < 2 and common_ngram < 2:
        return None
    if late_anchor_gain < 2 and len(late.tokens) <= len(early.tokens) + 1:
        return None
    if token_similarity < 0.24 and len(common_anchors) < 2:
        return None

    early_failure = _failure_score_for_tokens(
        early.tokens,
        duration=early_duration,
        attempt_count=1,
    )
    confidence = 0.74
    confidence += min(0.08, common_prefix * 0.025)
    confidence += min(0.08, len(common_anchors) * 0.025)
    confidence += 0.05 if common_ngram >= 2 else 0.0
    confidence += 0.04 if early_duration <= 2.5 else 0.0
    confidence += min(0.05, late_anchor_gain * 0.012)
    confidence += min(0.04, early_failure * 0.04)
    confidence = round(min(0.94, confidence), 3)
    effective_threshold = threshold - 0.15 if mode == "aggressive" else threshold
    removed_range = TimedRange(
        source_index=early.source_index,
        start=round(max(0.0, early.start - padding_sec), 3),
        end=round(min(source_durations[early.source_index], early.end + padding_sec), 3),
    )
    chosen_range = TimedRange(
        source_index=late.source_index,
        start=round(max(0.0, late.start - padding_sec), 3),
        end=round(min(source_durations[late.source_index], late.end + padding_sec), 3),
    )
    return RetakeDecision(
        status="applied" if confidence >= effective_threshold else "needs_review",
        reason="boundary_phrase_restart",
        confidence=confidence,
        attempts=[early, late],
        chosen_ranges=[chosen_range],
        removed_ranges=[removed_range],
        splice={
            "early_attempt_index": early.index,
            "late_attempt_index": late.index,
            "common_prefix": early.tokens[:common_prefix],
            "anchor_overlap": common_anchors,
        },
        group_type="boundary_phrase_restart",
        features={
            "early_source_index": early.source_index,
            "late_source_index": late.source_index,
            "early_duration": round(early_duration, 3),
            "late_duration": round(late_duration, 3),
            "common_prefix_len": common_prefix,
            "anchor_overlap": common_anchors,
            "anchor_overlap_count": len(common_anchors),
            "common_ngram": common_ngram,
            "token_similarity": round(token_similarity, 3),
            "late_anchor_gain": late_anchor_gain,
            "early_failure_score": early_failure,
            "removed_text": early.text,
            "kept_text": late.text,
        },
        kept_attempt_indexes=[late.index],
        removed_attempt_indexes=[early.index],
    )


def _boundary_prefix_rephrase_candidate(
    early: PhraseAttempt,
    late: PhraseAttempt,
    *,
    mode: RetakeMode,
    threshold: float,
    padding_sec: float,
    source_durations: list[float],
) -> RetakeDecision | None:
    if mode not in {"smart", "aggressive"}:
        return None
    if early.source_index == late.source_index:
        return None
    if early.source_index >= len(source_durations) or late.source_index >= len(source_durations):
        return None

    source_duration = source_durations[early.source_index]
    if source_duration <= 0:
        return None
    if source_duration - early.start > 12.0:
        return None

    common_prefix = _common_prefix_len(early.tokens, late.tokens)
    if common_prefix < 4:
        return None
    prefix_tokens = early.tokens[:common_prefix]
    prefix_anchor_count = sum(1 for token in prefix_tokens if token not in _BOUNDARY_GENERIC_TOKENS)
    if prefix_anchor_count < 1:
        return None

    early_suffix = early.tokens[common_prefix:]
    late_suffix = late.tokens[common_prefix:]
    if len(early_suffix) > 5 or len(late_suffix) < 3:
        return None
    if _has_contrast_start(early.tokens, late.tokens):
        return None

    early_anchors = {token for token in _anchor_tokens(early.tokens) if token not in _BOUNDARY_GENERIC_TOKENS}
    late_anchors = {token for token in _anchor_tokens(late.tokens) if token not in _BOUNDARY_GENERIC_TOKENS}
    late_anchor_gain = len(late_anchors - early_anchors)
    if late_anchor_gain < 2 and len(late.tokens) <= len(early.tokens) + 2:
        return None

    remove_start = round(max(0.0, early.start - padding_sec), 3)
    remove_end = round(min(source_duration, source_duration), 3)
    removed_duration = remove_end - remove_start
    if removed_duration <= 0.6 or removed_duration > 12.5:
        return None

    confidence = 0.79
    confidence += min(0.11, common_prefix * 0.022)
    confidence += min(0.06, late_anchor_gain * 0.012)
    confidence += 0.04 if len(early_suffix) <= 3 else 0.0
    confidence += 0.03 if late.end - late.start >= early.end - early.start else 0.0
    confidence = round(min(0.95, confidence), 3)
    effective_threshold = threshold - 0.15 if mode == "aggressive" else threshold

    return RetakeDecision(
        status="applied" if confidence >= effective_threshold else "needs_review",
        reason="boundary_prefix_rephrase_restart",
        confidence=confidence,
        attempts=[early, late],
        chosen_ranges=[
            TimedRange(
                source_index=late.source_index,
                start=round(max(0.0, late.start - padding_sec), 3),
                end=round(min(source_durations[late.source_index], late.end + padding_sec), 3),
            )
        ],
        removed_ranges=[
            TimedRange(
                source_index=early.source_index,
                start=remove_start,
                end=remove_end,
            )
        ],
        splice={
            "early_attempt_index": early.index,
            "late_attempt_index": late.index,
            "common_prefix": prefix_tokens,
        },
        group_type="boundary_prefix_rephrase_restart",
        features={
            "early_source_index": early.source_index,
            "late_source_index": late.source_index,
            "common_prefix_len": common_prefix,
            "prefix_anchor_count": prefix_anchor_count,
            "late_anchor_gain": late_anchor_gain,
            "early_suffix_tokens": early_suffix,
            "late_suffix_tokens": late_suffix,
            "removed_duration": round(removed_duration, 3),
            "removed_text": early.text,
            "kept_text": late.text,
        },
        kept_attempt_indexes=[late.index],
        removed_attempt_indexes=[early.index],
    )


def build_boundary_phrase_restart_decisions(
    attempts_by_source: list[list[PhraseAttempt]],
    *,
    mode: RetakeMode,
    threshold: float,
    padding_sec: float,
    source_durations: list[float],
) -> list[RetakeDecision]:
    decisions: list[RetakeDecision] = []
    for source_index in range(len(attempts_by_source) - 1):
        early_candidates = attempts_by_source[source_index][-4:]
        late_candidates = attempts_by_source[source_index + 1][:1]
        for early in early_candidates:
            for late in late_candidates:
                prefix_decision = _boundary_prefix_rephrase_candidate(
                    early,
                    late,
                    mode=mode,
                    threshold=threshold,
                    padding_sec=padding_sec,
                    source_durations=source_durations,
                )
                if prefix_decision is not None:
                    decisions.append(prefix_decision)
        for early in attempts_by_source[source_index][-1:]:
            for late in late_candidates:
                decision = _boundary_phrase_restart_candidate(
                    early,
                    late,
                    mode=mode,
                    threshold=threshold,
                    padding_sec=padding_sec,
                    source_durations=source_durations,
                )
                if decision is not None:
                    decisions.append(decision)
    decisions.sort(key=lambda decision: (decision.confidence, -decision.removed_ranges[0].start), reverse=True)
    selected: list[RetakeDecision] = []
    for decision in decisions:
        if any(_ranges_overlap(decision.removed_ranges, selected_decision.removed_ranges) for selected_decision in selected):
            continue
        selected.append(decision)
    return sorted(selected, key=lambda decision: (decision.removed_ranges[0].source_index, decision.removed_ranges[0].start))


def _span_contrast_pair(left: RepairSpan, right: RepairSpan) -> bool:
    if len(left.attempts) != 1 or len(right.attempts) != 1:
        return False
    return _is_full_clean_contrast_pair(left.attempts[0], right.attempts[0])


def _restart_repair_features(early: RepairSpan, late: RepairSpan) -> dict[str, Any] | None:
    if early.source_index != late.source_index:
        return None
    gap = late.start - early.end
    if gap < 0 or gap > _REPAIR_COMPARE_GAP_SEC:
        return None
    if _span_contrast_pair(early, late):
        return None

    early_anchors = set(_anchor_tokens(early.tokens))
    late_anchors = set(_anchor_tokens(late.tokens))
    common_anchors = sorted(early_anchors & late_anchors)
    anchor_coverage = len(common_anchors) / max(1, len(early_anchors))
    common_ngram = _longest_common_ngram_size(early.tokens, late.tokens)
    token_similarity = _token_similarity(early.tokens, late.tokens)
    if len(common_anchors) < 2 and common_ngram < 2 and token_similarity < 0.34:
        return None

    early_repeated = _repeated_ngram_count(early.tokens)
    late_repeated = _repeated_ngram_count(late.tokens)
    early_suspicious = _suspicious_asr_tokens(early.tokens)
    late_suspicious = _suspicious_asr_tokens(late.tokens)
    early_failure = _span_failure_score(early)
    late_failure = _span_failure_score(late)
    early_connective_ratio = _connective_ratio(early.tokens)
    late_connective_ratio = _connective_ratio(late.tokens)
    late_compact = late.duration <= min(8.5, max(6.5, early.duration * 0.7))
    late_cleaner = late_failure + 0.35 < early_failure or (
        late_compact and early_failure >= 0.45 and late_repeated <= early_repeated
    )
    has_restart_marker = (
        early_repeated > 0
        or bool(early_suspicious)
        or (early.duration >= 7.0 and len(early.attempts) > 1 and len(common_anchors) >= 2)
        or (early_connective_ratio >= 0.38 and early.duration >= 5.0)
    )
    if not has_restart_marker or not late_cleaner or not late_compact:
        return None

    suffix_gain = max(0, len(late.tokens) - len(common_anchors))
    confidence = 0.58
    confidence += min(0.16, len(common_anchors) * 0.035)
    confidence += 0.06 if common_ngram >= 2 else 0.0
    confidence += min(0.16, max(0.0, early_failure - late_failure) * 0.08)
    confidence += 0.08 if late.duration <= early.duration * 0.7 else 0.0
    confidence += 0.04 if early_repeated > 0 else 0.0
    confidence += min(0.06, suffix_gain * 0.008)
    confidence -= min(0.08, len(late_suspicious) * 0.03)

    return {
        "confidence": round(min(0.98, max(0.0, confidence)), 3),
        "gap_sec": round(gap, 3),
        "anchor_overlap": common_anchors,
        "anchor_overlap_count": len(common_anchors),
        "early_anchor_count": len(early_anchors),
        "late_anchor_count": len(late_anchors),
        "anchor_coverage": round(anchor_coverage, 3),
        "common_ngram": common_ngram,
        "token_similarity": round(token_similarity, 3),
        "early_failure_score": early_failure,
        "late_failure_score": late_failure,
        "early_repeated_ngrams": early_repeated,
        "late_repeated_ngrams": late_repeated,
        "early_suspicious_tokens": early_suspicious,
        "late_suspicious_tokens": late_suspicious,
        "early_connective_ratio": round(early_connective_ratio, 3),
        "late_connective_ratio": round(late_connective_ratio, 3),
        "early_duration": round(early.duration, 3),
        "late_duration": round(late.duration, 3),
        "late_suffix_token_gain": suffix_gain,
    }


def _build_restart_repair_decision(
    *,
    early: RepairSpan,
    late: RepairSpan,
    features: dict[str, Any],
    mode: RetakeMode,
    threshold: float,
    padding_sec: float,
    transcript: Transcript,
    transcript_source: str,
) -> RetakeDecision:
    restart_token_index = _first_repeated_ngram_start_index(early.tokens)
    restart_start = (
        _span_token_start(early, transcript, restart_token_index)
        if restart_token_index is not None
        else None
    )
    remove_start = early.start
    remove_start_padding = padding_sec
    if restart_start is not None and restart_start - early.start >= 0.5:
        remove_start = restart_start
        remove_start_padding = 0.0
        features = {
            **features,
            "early_restart_token_index": restart_token_index,
            "early_restart_start": round(restart_start, 3),
        }
    removed_end = max(early.end + padding_sec, late.start - padding_sec)
    chosen_range = TimedRange(
        source_index=late.source_index,
        start=round(max(0.0, late.start - padding_sec), 3),
        end=round(min(transcript.duration, late.end + padding_sec), 3),
    )
    removed_range = TimedRange(
        source_index=early.source_index,
        start=round(max(0.0, remove_start - remove_start_padding), 3),
        end=round(min(transcript.duration, removed_end), 3),
    )
    effective_threshold = threshold - 0.15 if mode == "aggressive" else threshold
    confidence = float(features["confidence"])
    review_only_large_low_coverage = (
        mode == "smart"
        and removed_range.duration >= 8.0
        and float(features.get("anchor_coverage", 1.0)) < 0.25
        and int(features.get("common_ngram", 0)) < 3
    )
    if review_only_large_low_coverage:
        features = {
            **features,
            "risk": "large_low_coverage_restart_repair",
            "removed_duration": round(removed_range.duration, 3),
        }
    status = "applied" if confidence >= effective_threshold and not review_only_large_low_coverage else "needs_review"
    return RetakeDecision(
        status=status,
        reason="restart_repair",
        confidence=confidence,
        attempts=[*early.attempts, *late.attempts],
        chosen_ranges=[chosen_range],
        removed_ranges=[removed_range],
        splice=None,
        group_type="restart_repair",
        features=features,
        kept_attempt_indexes=late.attempt_indexes,
        removed_attempt_indexes=early.attempt_indexes,
        transcript_source=transcript_source,
    )


def build_restart_repair_decisions(
    attempts: list[PhraseAttempt],
    *,
    mode: RetakeMode,
    threshold: float,
    transcript: Transcript,
    padding_sec: float,
    exact_groups: list[list[PhraseAttempt]],
    transcript_source: str = "primary",
) -> list[RetakeDecision]:
    if mode not in {"smart", "aggressive"}:
        return []

    exact_attempt_indexes = {attempt.index for group in exact_groups for attempt in group}
    spans = [
        span
        for span in _repair_spans_from_attempts(attempts)
        if not any(index in exact_attempt_indexes for index in span.attempt_indexes)
    ]
    candidates: list[RetakeDecision] = []
    for early in spans:
        for late in spans:
            if late.start <= early.end:
                continue
            if set(early.attempt_indexes) & set(late.attempt_indexes):
                continue
            features = _restart_repair_features(early, late)
            if features is None:
                continue
            candidates.append(
                _build_restart_repair_decision(
                    early=early,
                    late=late,
                    features=features,
                    mode=mode,
                    threshold=threshold,
                    padding_sec=padding_sec,
                    transcript=transcript,
                    transcript_source=transcript_source,
                )
            )

    selected: list[RetakeDecision] = []
    used_attempt_indexes: set[int] = set()
    candidates.sort(
        key=lambda decision: (
            decision.confidence,
            len(decision.removed_attempt_indexes or []),
            -decision.chosen_ranges[0].start,
            sum(rng.duration for rng in decision.removed_ranges),
            min(6.5, sum(rng.duration for rng in decision.chosen_ranges)),
        ),
        reverse=True,
    )
    for decision in candidates:
        attempt_indexes = {attempt.index for attempt in decision.attempts}
        if used_attempt_indexes & attempt_indexes:
            continue
        selected.append(decision)
        used_attempt_indexes.update(attempt_indexes)
    return sorted(selected, key=lambda decision: decision.removed_ranges[0].start)


def _confidence_for_group(group: list[PhraseAttempt], chosen: PhraseAttempt, *, spliced: bool) -> float:
    others = [attempt for attempt in group if attempt.index != chosen.index]
    best_similarity = max((_token_similarity(chosen.tokens, attempt.tokens) for attempt in others), default=0.0)
    second_score = max((attempt.score for attempt in others), default=chosen.score)
    margin = max(0.0, chosen.score - second_score)
    confidence = 0.58 + min(0.24, margin / 8.0) + best_similarity * 0.18
    if best_similarity >= 0.9:
        confidence += 0.06
    if spliced:
        confidence += 0.14
    return round(min(confidence, 0.98), 3)


def _range_for_attempt(attempt: PhraseAttempt, *, padding_sec: float, transcript_duration: float) -> TimedRange:
    return TimedRange(
        source_index=attempt.source_index,
        start=round(max(0.0, attempt.start - padding_sec), 3),
        end=round(min(transcript_duration, attempt.end + padding_sec), 3),
    )


def _group_removed_range(group: list[PhraseAttempt], *, padding_sec: float, transcript_duration: float) -> TimedRange:
    return TimedRange(
        source_index=group[0].source_index,
        start=round(max(0.0, min(attempt.start for attempt in group) - padding_sec), 3),
        end=round(min(transcript_duration, max(attempt.end for attempt in group) + padding_sec), 3),
    )


def _build_splice(
    group: list[PhraseAttempt],
    *,
    padding_sec: float,
    transcript: Transcript,
) -> tuple[list[TimedRange], dict[str, Any], PhraseAttempt] | None:
    for later in reversed(group[1:]):
        restart_len = min(4, len(later.tokens))
        if restart_len < 2:
            continue
        for prefix in reversed([attempt for attempt in group if attempt.index < later.index]):
            for length in range(restart_len, 1, -1):
                match_index = _find_subsequence(prefix.tokens, later.tokens[:length])
                if match_index is None or match_index == 0:
                    continue
                if match_index < 3:
                    continue
                prefix_words = [
                    word
                    for word in transcript.words
                    if prefix.start <= word.start and word.end <= prefix.end
                ]
                meaningful_positions = [
                    idx
                    for idx, word in enumerate(prefix_words)
                    if normalize_token(word.word) and normalize_token(word.word) not in _FILLER_TOKENS
                ]
                if match_index >= len(meaningful_positions):
                    continue
                split_word_index = meaningful_positions[match_index]
                split_sec = prefix_words[split_word_index].start
                if split_sec - prefix.start < 0.8 or later.end - later.start < 0.8:
                    continue
                ranges = [
                    TimedRange(
                        source_index=prefix.source_index,
                        start=round(max(0.0, prefix.start - padding_sec), 3),
                        end=round(max(prefix.start, split_sec + padding_sec), 3),
                    ),
                    TimedRange(
                        source_index=later.source_index,
                        start=round(max(0.0, later.start - padding_sec), 3),
                        end=round(min(transcript.duration, later.end + padding_sec), 3),
                    ),
                ]
                splice = {
                    "prefix_attempt_index": prefix.index,
                    "suffix_attempt_index": later.index,
                    "overlap_tokens": later.tokens[:length],
                    "split_sec": round(split_sec, 3),
                }
                return ranges, splice, prefix
    return None


def _build_suffix_repair_splice(
    group: list[PhraseAttempt],
    chosen: PhraseAttempt,
    *,
    attempts: list[PhraseAttempt],
    padding_sec: float,
    transcript: Transcript,
) -> tuple[list[TimedRange], dict[str, Any], list[int]] | None:
    group_indexes = {attempt.index for attempt in group}
    min_split_index = max(3, int(len(chosen.tokens) * 0.45))
    for later in attempts:
        if later.index in group_indexes or later.index <= chosen.index:
            continue
        if later.source_index != chosen.source_index:
            continue
        if later.start - chosen.end < 0 or later.start - chosen.end > 2.25:
            continue
        later_start_tokens = [token for token in later.tokens[:2] if token not in _CONNECTIVE_TOKENS]
        if not later_start_tokens:
            later_start_tokens = later.tokens[:1]
        split_index = next(
            (
                index
                for index in range(min_split_index, len(chosen.tokens))
                if chosen.tokens[index] in later_start_tokens
            ),
            None,
        )
        if split_index is None:
            continue

        chosen_suffix = chosen.tokens[split_index:]
        common_positions = [
            index
            for index, token in enumerate(later.tokens)
            if token in chosen_suffix and token not in _CONNECTIVE_TOKENS
        ]
        if len(common_positions) < 2:
            continue
        last_common = max(common_positions)
        suffix_end_index = last_common
        if suffix_end_index + 1 < len(later.tokens) and later.tokens[suffix_end_index + 1] not in _CONNECTIVE_TOKENS:
            suffix_end_index += 1
        split_sec = _attempt_token_start(chosen, transcript, split_index)
        suffix_end = _attempt_token_end(later, transcript, suffix_end_index)
        if split_sec is None or suffix_end is None:
            continue
        if split_sec - chosen.start < 0.8 or suffix_end - later.start < 0.5:
            continue

        ranges = [
            TimedRange(
                source_index=chosen.source_index,
                start=round(max(0.0, chosen.start - padding_sec), 3),
                end=round(max(chosen.start, split_sec), 3),
            ),
            TimedRange(
                source_index=later.source_index,
                start=round(max(0.0, later.start - padding_sec), 3),
                end=round(min(transcript.duration, suffix_end + padding_sec), 3),
            ),
        ]
        splice = {
            "prefix_attempt_index": chosen.index,
            "suffix_attempt_index": later.index,
            "split_token": chosen.tokens[split_index],
            "split_sec": round(split_sec, 3),
            "suffix_end_sec": round(suffix_end, 3),
            "overlap_tokens": [token for token in later.tokens[: suffix_end_index + 1] if token in chosen_suffix],
        }
        return ranges, splice, [chosen.index, later.index]
    return None


def _build_prefix_restart_repair(
    group: list[PhraseAttempt],
    *,
    attempts: list[PhraseAttempt],
    padding_sec: float,
    transcript: Transcript,
) -> tuple[list[TimedRange], dict[str, Any], list[int], PhraseAttempt, float] | None:
    group_indexes = {attempt.index for attempt in group}
    attempts_by_index = {attempt.index: attempt for attempt in attempts}
    candidates: list[tuple[float, list[TimedRange], dict[str, Any], list[int], PhraseAttempt]] = []

    for early in group:
        early_duration = early.end - early.start
        if len(early.tokens) > 4 or early_duration > 2.8:
            continue
        for partial in group:
            if partial.index <= early.index:
                continue
            if partial.source_index != early.source_index:
                continue
            if partial.start - early.end < 0 or partial.start - early.end > _REPAIR_COMPARE_GAP_SEC:
                continue
            if len(partial.tokens) > 2:
                continue

            repair_attempts = [partial]
            next_index = partial.index + 1
            while next_index in attempts_by_index and len(repair_attempts) < _REPAIR_MAX_SPAN_ATTEMPTS:
                previous = repair_attempts[-1]
                next_attempt = attempts_by_index[next_index]
                if next_attempt.source_index != partial.source_index:
                    break
                if next_attempt.start - previous.end > _REPAIR_SPAN_GAP_SEC:
                    break
                if next_attempt.end - partial.start > _REPAIR_MAX_SPAN_SEC:
                    break
                repair_attempts.append(next_attempt)
                next_index += 1

            if len(repair_attempts) < 2:
                continue
            repair_span = _repair_span_from_attempts(repair_attempts)
            prefix_len = _common_prefix_len(early.tokens, repair_span.tokens)
            required_prefix = min(2, len(early.tokens), len(repair_span.tokens))
            if prefix_len < required_prefix:
                continue

            early_anchors = set(_anchor_tokens(early.tokens))
            repair_anchors = set(_anchor_tokens(repair_span.tokens))
            anchor_gain = len(repair_anchors - early_anchors)
            token_gain = len(repair_span.tokens) - len(early.tokens)
            if anchor_gain < 2 and token_gain < 2:
                continue
            if _has_contrast_start(early.tokens, repair_span.tokens):
                continue

            confidence = 0.79
            confidence += min(0.07, prefix_len * 0.025)
            confidence += min(0.06, max(anchor_gain, token_gain) * 0.015)
            confidence += 0.03 if partial.index not in group_indexes else 0.0
            confidence = round(min(0.94, confidence), 3)
            ranges = [
                TimedRange(
                    source_index=repair_span.source_index,
                    start=round(max(0.0, repair_span.start - padding_sec), 3),
                    end=round(min(transcript.duration, repair_span.end + padding_sec), 3),
                )
            ]
            splice = {
                "prefix_attempt_index": early.index,
                "repair_attempt_indexes": repair_span.attempt_indexes,
                "prefix_overlap_tokens": repair_span.tokens[:prefix_len],
                "anchor_gain": anchor_gain,
                "token_gain": token_gain,
            }
            candidates.append((confidence, ranges, splice, repair_span.attempt_indexes, partial))

    if not candidates:
        return None
    confidence, ranges, splice, kept_indexes, kept_attempt = max(
        candidates,
        key=lambda item: (
            item[0],
            len(item[3]),
            sum(rng.duration for rng in item[1]),
        ),
    )
    return ranges, splice, kept_indexes, kept_attempt, confidence


def _build_decision(
    group: list[PhraseAttempt],
    *,
    attempts: list[PhraseAttempt],
    mode: RetakeMode,
    threshold: float,
    transcript: Transcript,
    padding_sec: float,
    transcript_source: str,
) -> RetakeDecision:
    chosen = max(group, key=lambda attempt: attempt.score)
    chosen_ranges = [_range_for_attempt(chosen, padding_sec=padding_sec, transcript_duration=transcript.duration)]
    splice = None
    spliced = False
    reason = "best_late_clean_attempt"
    forced_confidence: float | None = None

    if mode in {"smart", "aggressive"}:
        prefix_repair_candidate = _build_prefix_restart_repair(
            group,
            attempts=attempts,
            padding_sec=padding_sec,
            transcript=transcript,
        )
        if prefix_repair_candidate is not None:
            chosen_ranges, splice, prefix_kept_indexes, chosen, forced_confidence = prefix_repair_candidate
            spliced = True
            reason = "prefix_restart_repair"
        else:
            splice_candidate = _build_splice(group, padding_sec=padding_sec, transcript=transcript)
            if splice_candidate is not None:
                chosen_ranges, splice, prefix_attempt = splice_candidate
                chosen = prefix_attempt
                spliced = True
                reason = "smart_splice_restart"
            else:
                suffix_candidate = _build_suffix_repair_splice(
                    group,
                    chosen,
                    attempts=attempts,
                    padding_sec=padding_sec,
                    transcript=transcript,
                )
                if suffix_candidate is not None:
                    chosen_ranges, splice, suffix_kept_indexes = suffix_candidate
                    spliced = True
                    reason = "suffix_repair_splice"

    confidence = forced_confidence or _confidence_for_group(group, chosen, spliced=spliced)
    effective_threshold = threshold - 0.15 if mode == "aggressive" else threshold
    status = "applied" if confidence >= effective_threshold else "needs_review"
    if reason == "prefix_restart_repair":
        kept_indexes = prefix_kept_indexes
    elif reason == "suffix_repair_splice":
        kept_indexes = suffix_kept_indexes
    elif splice is not None:
        kept_indexes = [
            int(splice["prefix_attempt_index"]),
            int(splice["suffix_attempt_index"]),
        ]
    else:
        kept_indexes = [chosen.index]
    removed_indexes = [attempt.index for attempt in group if attempt.index not in kept_indexes]

    return RetakeDecision(
        status=status,
        reason=reason,
        confidence=confidence,
        attempts=group,
        chosen_ranges=chosen_ranges,
        removed_ranges=[
            _group_removed_range(group, padding_sec=padding_sec, transcript_duration=transcript.duration)
        ],
        splice=splice,
        group_type=reason if reason in {"prefix_restart_repair", "suffix_repair_splice"} else "exact_retake",
        features={
            "best_similarity": round(
                max(
                    (_token_similarity(chosen.tokens, attempt.tokens) for attempt in group if attempt.index != chosen.index),
                    default=0.0,
                ),
                3,
            ),
            "score_margin": round(
                max(
                    0.0,
                    chosen.score
                    - max((attempt.score for attempt in group if attempt.index != chosen.index), default=chosen.score),
                ),
                3,
            ),
            **({"suffix_repair": splice} if reason == "suffix_repair_splice" else {}),
            **({"prefix_restart_repair": splice} if reason == "prefix_restart_repair" else {}),
        },
        kept_attempt_indexes=kept_indexes,
        removed_attempt_indexes=removed_indexes,
        transcript_source=transcript_source,
    )


def _subtract_range(segments: list[TimedRange], remove: TimedRange) -> list[TimedRange]:
    output: list[TimedRange] = []
    for segment in segments:
        if segment.source_index != remove.source_index or segment.end <= remove.start or segment.start >= remove.end:
            output.append(segment)
            continue
        if segment.start < remove.start:
            output.append(TimedRange(segment.source_index, segment.start, round(remove.start, 3)))
        if segment.end > remove.end:
            output.append(TimedRange(segment.source_index, round(remove.end, 3), segment.end))
    return [segment for segment in output if segment.end - segment.start >= _MIN_SPEECH_FRAGMENT_SEC]


def _intersect_ranges(candidates: list[TimedRange], base_segments: list[TimedRange]) -> list[TimedRange]:
    output: list[TimedRange] = []
    for candidate in candidates:
        for base in base_segments:
            if candidate.source_index != base.source_index:
                continue
            start = max(candidate.start, base.start)
            end = min(candidate.end, base.end)
            if end - start >= _MIN_SPEECH_FRAGMENT_SEC:
                output.append(TimedRange(candidate.source_index, round(start, 3), round(end, 3)))
    return output


def _merge_ranges(segments: list[TimedRange]) -> list[TimedRange]:
    ordered = sorted(segments, key=lambda segment: (segment.source_index, segment.start, segment.end))
    merged: list[TimedRange] = []
    for segment in ordered:
        if not merged or segment.source_index != merged[-1].source_index or segment.start - merged[-1].end > 0.08:
            merged.append(segment)
            continue
        previous = merged[-1]
        merged[-1] = TimedRange(
            source_index=previous.source_index,
            start=previous.start,
            end=max(previous.end, segment.end),
        )
    return merged


def apply_retake_decisions(
    speech_segments: list[TimedRange],
    decisions: list[RetakeDecision],
) -> list[TimedRange]:
    planned = list(speech_segments)
    for decision in decisions:
        if decision.status != "applied":
            continue
        for removed in decision.removed_ranges:
            planned = _subtract_range(planned, removed)
        planned.extend(_intersect_ranges(decision.chosen_ranges, speech_segments))
    return _merge_ranges(planned)


def _base_segments_from_speech(speech_segments: list[Any], *, source_index: int) -> list[TimedRange]:
    return [
        TimedRange(
            source_index=_source_index(segment, source_index),
            start=round(_start(segment), 3),
            end=round(_end(segment), 3),
        )
        for segment in speech_segments
        if _source_index(segment, source_index) == source_index
        and _end(segment) - _start(segment) >= _MIN_SPEECH_FRAGMENT_SEC
    ]


def _ranges_overlap(left: list[TimedRange], right: list[TimedRange]) -> bool:
    for left_range in left:
        for right_range in right:
            if left_range.source_index != right_range.source_index:
                continue
            if left_range.end > right_range.start and right_range.end > left_range.start:
                return True
    return False


def _decision_priority(decision: RetakeDecision) -> tuple[float, int, float]:
    return (
        decision.confidence,
        1 if decision.transcript_source == "primary" else 0,
        sum(rng.duration for rng in decision.removed_ranges),
    )


def merge_retake_decisions(decisions: list[RetakeDecision]) -> list[RetakeDecision]:
    applied = [decision for decision in decisions if decision.status == "applied"]
    selected: list[RetakeDecision] = []
    for decision in sorted(applied, key=_decision_priority, reverse=True):
        if any(_ranges_overlap(decision.removed_ranges, selected_decision.removed_ranges) for selected_decision in selected):
            continue
        selected.append(decision)

    selected_ids = {id(decision) for decision in selected}
    output = [decision for decision in decisions if id(decision) in selected_ids or decision.status != "applied"]
    return sorted(output, key=lambda decision: (decision.removed_ranges[0].start if decision.removed_ranges else 0.0))


def build_retake_decisions(
    transcript: Transcript,
    *,
    mode: RetakeMode = "smart",
    confidence_threshold: float = 0.78,
    source_index: int = 0,
    padding_sec: float = 0.12,
    transcript_source: str = "primary",
) -> list[RetakeDecision]:
    if mode == "off" or not transcript.words:
        return []

    attempts = phrase_attempts_from_transcript(transcript, source_index=source_index)
    groups = group_retake_attempts(attempts, aggressive=mode == "aggressive")
    decisions = [
        _build_decision(
            group,
            attempts=attempts,
            mode=mode,
            threshold=confidence_threshold,
            transcript=transcript,
            padding_sec=padding_sec,
            transcript_source=transcript_source,
        )
        for group in groups
    ]
    decisions.extend(
        build_local_phrase_restart_decisions(
            attempts,
            mode=mode,
            transcript=transcript,
            padding_sec=padding_sec,
        )
    )
    decisions.extend(
        build_correction_marker_rephrase_decisions(
            attempts,
            mode=mode,
            transcript=transcript,
            padding_sec=padding_sec,
        )
    )
    decisions.extend(
        build_restart_repair_decisions(
            attempts,
            mode=mode,
            threshold=confidence_threshold,
            transcript=transcript,
            padding_sec=padding_sec,
            exact_groups=groups,
            transcript_source=transcript_source,
        )
    )
    return decisions


def plan_retakes_from_decisions(
    speech_segments: list[Any],
    decisions: list[RetakeDecision],
    *,
    mode: RetakeMode = "smart",
    confidence_threshold: float = 0.78,
    source_index: int = 0,
) -> RetakePlan:
    base_segments = _base_segments_from_speech(speech_segments, source_index=source_index)
    merged_decisions = merge_retake_decisions(decisions)
    planned_segments = apply_retake_decisions(base_segments, merged_decisions)
    return RetakePlan(
        mode=mode,
        confidence_threshold=confidence_threshold,
        source_index=source_index,
        speech_segments=planned_segments,
        decisions=merged_decisions,
    )


def plan_retakes(
    transcript: Transcript,
    speech_segments: list[Any],
    *,
    mode: RetakeMode = "smart",
    confidence_threshold: float = 0.78,
    source_index: int = 0,
    padding_sec: float = 0.12,
) -> RetakePlan:
    base_segments = _base_segments_from_speech(speech_segments, source_index=source_index)
    if mode == "off" or not transcript.words:
        return RetakePlan(
            mode=mode,
            confidence_threshold=confidence_threshold,
            source_index=source_index,
            speech_segments=base_segments,
            decisions=[],
        )

    decisions = build_retake_decisions(
        transcript,
        mode=mode,
        confidence_threshold=confidence_threshold,
        source_index=source_index,
        padding_sec=padding_sec,
        transcript_source="primary",
    )
    return plan_retakes_from_decisions(
        speech_segments,
        decisions,
        mode=mode,
        confidence_threshold=confidence_threshold,
        source_index=source_index,
    )
