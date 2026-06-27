# -*- coding: utf-8 -*-
"""Unit tests for the talking-head QA duplicate detector (find_duplicates).

The detector must flag restarts/false-starts the retake stage leaves in (the two
kinds that shipped in d40 v1) while ignoring legit cross-sentence echoes and
anaphora — the discriminator is "no sentence terminator between the repeats".
"""
from pipelines.qa_talking_head import find_duplicates


def _words(text, step=0.3, dur=0.25):
    """Turn a space-separated string into timed word dicts, punctuation kept."""
    return [
        {"word": " " + tok, "start": round(i * step, 3), "end": round(i * step + dur, 3)}
        for i, tok in enumerate(text.split())
    ]


def test_detects_stutter_restart_cue_word():
    # «… что выбрать второе это ты второе то что …» — заминка на слове «второе»
    found = find_duplicates(_words(
        "идей много не знаешь что выбрать второе это ты второе то что много думаешь"))
    assert found, "ожидали находку дубля-заминки"
    assert any("второе" in lab for _, _, lab in found)


def test_detects_reordered_restart_bigram():
    # «… скажу у меня решение которое по маской скажу на своём примере у меня решение …»
    found = find_duplicates(_words(
        "найти проблему скажу у меня решение которое по маской "
        "скажу на своём примере у меня решение помогает"))
    assert found, "ожидали находку переставленного рестарта"


def test_ignores_echo_across_sentence_boundary():
    # «… прийти вообще. почему вообще денежка …» — эхо слова через точку, не дубль
    assert find_duplicates(_words(
        "как к этому прийти вообще. почему вообще денежка ещё при тебе")) == []


def test_ignores_anaphora_across_sentences():
    # «когда ты нашел … . … . если ты нашел …» — анафора через целые предложения
    assert find_duplicates(_words(
        "когда ты нашел проблемы ищешь конкурентов. можно изучить в интернете. "
        "если ты нашел такую боль конкурентов")) == []


def test_clean_text_has_no_duplicates():
    assert find_duplicates(_words(
        "привет как дела сегодня отличная погода на улице тепло и солнечно")) == []


def test_far_apart_repeat_is_not_flagged():
    # одинаковая биграмма «у меня», но > DUP_GAP_SEC по времени — не рестарт
    words = [
        {"word": " у", "start": 0.0, "end": 0.2},
        {"word": " меня", "start": 0.3, "end": 0.6},
        {"word": " дела", "start": 0.7, "end": 1.0},
        {"word": " у", "start": 9.0, "end": 9.2},
        {"word": " меня", "start": 9.3, "end": 9.6},
        {"word": " мысли", "start": 9.7, "end": 10.0},
    ]
    assert find_duplicates(words) == []


def test_enumeration_first_second_third_is_clean():
    # перечисление первое/второе/третье (каждое по разу) — не дубль
    assert find_duplicates(_words(
        "первое паралич выбора второе ты много думаешь третье выбираешь ради денег")) == []
