from __future__ import annotations

from src.schemas import Transcript, Word
from pipelines.reel_matrix import (
    Combo,
    build_clean_filter,
    classify_clip_role,
    enumerate_combos,
    merge_transcripts,
    speech_segments_from_silences,
)


def test_classify_cta_from_closing_language() -> None:
    role, conf, _ = classify_clip_role("Попробовать можно бесплатно, ссылка в шапке профиля.")
    assert role == "cta"
    assert conf >= 0.5


def test_classify_hook_from_opening_language() -> None:
    role, _, _ = classify_clip_role("Мне друг скинул жёсткую имбу, как найти работу за месяц.")
    assert role == "hook"


def test_classify_tip_default_for_advice() -> None:
    role, _, _ = classify_clip_role("Адаптируй резюме под каждую вакансию и проверь структуру.")
    assert role == "tip"


def test_filename_hint_overrides_content() -> None:
    # content looks like a tip, but the filename says cta
    role, conf, reason = classify_clip_role("сделай вот так", filename="cta_2.mov")
    assert role == "cta"
    assert conf == 1.0
    assert "filename" in reason


def test_enumerate_combos_counts_hook_cta_and_tip_permutations() -> None:
    combos = enumerate_combos(["h1", "h2"], ["t1", "t2", "t3"], ["c1", "c2"])
    # 2 hooks x 2 ctas x 3! tip orders
    assert len(combos) == 2 * 2 * 6
    assert all(isinstance(c, Combo) for c in combos)
    assert all(set(c.tips) == {"t1", "t2", "t3"} for c in combos)
    # ids are unique
    assert len({c.id for c in combos}) == len(combos)


def test_enumerate_combos_fixed_order_keeps_single_tip_sequence() -> None:
    combos = enumerate_combos(["h1"], ["t1", "t2", "t3"], ["c1"], permute_tips=False)
    assert len(combos) == 1
    assert combos[0].tips == ("t1", "t2", "t3")


def test_merge_transcripts_offsets_words_onto_one_timeline() -> None:
    a = Transcript(words=[Word(word="один", start=0.0, end=0.5)], full_text="один", duration=1.0)
    b = Transcript(words=[Word(word="два", start=0.0, end=0.4)], full_text="два", duration=0.8)
    merged = merge_transcripts([a, b])
    assert [w.word for w in merged.words] == ["один", "два"]
    assert merged.words[1].start == 1.0      # second clip shifted by first duration
    assert merged.duration == 1.8


def test_speech_segments_drop_silences_and_pad() -> None:
    # speech 0..2, silence 2..4 (long), speech 4..6
    segs = speech_segments_from_silences([(2.0, 4.0)], duration=6.0,
                                         head_pad=0.0, tail_pad=0.0)
    assert segs == [(0.0, 2.0), (4.0, 6.0)]


def test_build_clean_filter_tonemaps_hdr_and_crops_vertical() -> None:
    fc = build_clean_filter([(0.0, 1.0), (2.0, 3.0)], hdr=True)
    assert "tonemap=mobius" in fc
    assert "crop=1080:1920" in fc
    assert "concat=n=2:v=1:a=1[v][a]" in fc


def test_build_clean_filter_skips_tonemap_for_sdr() -> None:
    fc = build_clean_filter([(0.0, 1.0)], hdr=False)
    assert "tonemap" not in fc
    assert "concat=n=1:v=1:a=1[v][a]" in fc
