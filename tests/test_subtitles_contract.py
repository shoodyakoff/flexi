from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

import yaml

from src.cli import _load_script, _run_build
from src.schemas import AssetEntry, Config, HookMontageDiagnostics, SubtitleSafeBoxConfig, SubtitleStyle, Transcript, Word
from src.subtitles import _clean_word_text, transcript_to_ass
from src.tts_text import VoiceoverTextPlan

ROOT = Path(__file__).resolve().parents[1]


def _style(**overrides) -> SubtitleStyle:
    values = {
        "font": "Druk Wide Cyr",
        "size": 85,
        "primary_color": "&H001574F9",
        "outline_color": "&H66000000",
        "outline": 3,
        "shadow": 1,
        "margin_v": 220,
        "uppercase": True,
        "strip_punctuation": True,
        "max_words_per_chunk": 1,
        "max_chunk_duration_sec": 1.15,
        "max_chunk_gap_sec": 0.18,
        "min_display_duration_sec": 0.32,
        "min_event_duration_warning_sec": 0.12,
        "end_hold_sec": 0.08,
        "fade_in_ms": 20,
        "fade_out_ms": 20,
        "split_long_words": True,
        "max_lines": 2,
        "attach_short_tokens": True,
        "attach_numbers": True,
        "forbid_two_content_words": True,
    }
    values.update(overrides)
    return SubtitleStyle(**values)


class SubtitleContractTest(unittest.TestCase):
    def test_clean_word_text_keeps_inline_connectors_between_word_chars(self) -> None:
        cases = {
            "4/5": "4/5",
            "4.5": "4.5",
            "email-рассылка.": "EMAIL-РАССЫЛКА",
            "денег/часов.": "ДЕНЕГ/ЧАСОВ",
            "3–5": "3–5",
            "обычный,": "ОБЫЧНЫЙ",
        }

        for raw, expected in cases.items():
            self.assertEqual(_clean_word_text(raw, uppercase=True, strip_punct=True), expected)

    def test_chunks_keep_all_words_on_screen(self) -> None:
        transcript = Transcript(
            words=[
                Word(word="Есть", start=0.00, end=0.08),
                Word(word="2", start=0.10, end=0.14),
                Word(word="способа.", start=0.16, end=0.26),
                Word(word="Первый", start=0.70, end=0.82),
            ],
            full_text="Есть 2 способа. Первый",
            duration=0.82,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "subs.ass"
            transcript_to_ass(
                transcript,
                _style(),
                out_path,
                safe_box=SubtitleSafeBoxConfig(side_padding_px=0),
            )
            ass = out_path.read_text(encoding="utf-8")

        self.assertIn("ЕСТЬ", ass)
        self.assertIn("2 СПОСОБА", ass)
        self.assertIn("ПЕРВЫЙ", ass)
        self.assertEqual(ass.count("Dialogue:"), 3)
        self.assertIn(r"\fad(20,20)", ass)

    def test_long_word_wraps_to_second_line_without_hyphen(self) -> None:
        transcript = Transcript(
            words=[
                Word(
                    word="сопроводительные",
                    start=0.00,
                    end=0.50,
                )
            ],
            full_text="сопроводительные",
            duration=0.50,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "subs.ass"
            transcript_to_ass(
                transcript,
                _style(long_word_split_min_chars=6),
                out_path,
                safe_box=SubtitleSafeBoxConfig(play_res_x=760, center_x=380, side_padding_px=0),
            )
            dialogue = [
                line
                for line in out_path.read_text(encoding="utf-8").splitlines()
                if line.startswith("Dialogue:")
            ][0]

        self.assertIn(r"\N", dialogue)
        self.assertNotIn(r"\fs", dialogue)
        self.assertNotIn("-", dialogue.rsplit(",", 1)[-1])

    def test_long_word_can_wrap_inside_short_phrase_without_font_shrink(self) -> None:
        transcript = Transcript(
            words=[
                Word(word="я", start=0.00, end=0.08),
                Word(word="проанализировал", start=0.10, end=0.58),
            ],
            full_text="я проанализировал",
            duration=0.58,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "subs.ass"
            transcript_to_ass(
                transcript,
                _style(long_word_split_min_chars=6),
                out_path,
                safe_box=SubtitleSafeBoxConfig(play_res_x=1080, center_x=540, side_padding_px=0),
            )
            dialogues = [
                line
                for line in out_path.read_text(encoding="utf-8").splitlines()
                if line.startswith("Dialogue:")
            ]

        self.assertEqual(len(dialogues), 1)
        self.assertIn("Я", dialogues[0])
        self.assertIn(r"\N", dialogues[0])
        self.assertNotIn(r"\fs", dialogues[0])

    def test_content_words_are_not_joined_together(self) -> None:
        cases = [
            ("получить быстрее", ["ПОЛУЧИТЬ", "БЫСТРЕЕ"]),
            ("следующую проблему", ["СЛЕДУЮЩУЮ", "ПРОБЛЕМУ"]),
            ("сопроводительные письма", ["СОПРОВОДИТЕЛЬНЫЕ", "ПИСЬМА"]),
        ]

        for text, expected in cases:
            words = text.split()
            transcript = Transcript(
                words=[
                    Word(word=word, start=index * 0.30, end=index * 0.30 + 0.20)
                    for index, word in enumerate(words)
                ],
                full_text=text,
                duration=len(words) * 0.30,
            )

            with tempfile.TemporaryDirectory() as tmp_dir:
                out_path = Path(tmp_dir) / "subs.ass"
                transcript_to_ass(
                    transcript,
                    _style(long_word_split_min_chars=6),
                    out_path,
                    safe_box=SubtitleSafeBoxConfig(play_res_x=1080, center_x=540, side_padding_px=0),
                )
                dialogues = [
                    line.rsplit(",", 1)[-1]
                    for line in out_path.read_text(encoding="utf-8").splitlines()
                    if line.startswith("Dialogue:")
                ]

            self.assertEqual(len(dialogues), len(expected), text)
            for actual, expected_word in zip(dialogues, expected):
                self.assertIn(expected_word, actual.replace(r"\N", ""))

    def test_helpers_and_numbers_can_attach_to_one_content_word(self) -> None:
        cases = [
            ("я проанализировал", ["Я ПРОАНАЛИЗИРОВАЛ"]),
            ("2 способа", ["2 СПОСОБА"]),
            ("за 3 минуты", ["ЗА 3 МИНУТЫ"]),
        ]

        for text, expected in cases:
            words = text.split()
            transcript = Transcript(
                words=[
                    Word(word=word, start=index * 0.12, end=index * 0.12 + 0.08)
                    for index, word in enumerate(words)
                ],
                full_text=text,
                duration=len(words) * 0.12,
            )

            with tempfile.TemporaryDirectory() as tmp_dir:
                out_path = Path(tmp_dir) / "subs.ass"
                transcript_to_ass(
                    transcript,
                    _style(long_word_split_min_chars=6),
                    out_path,
                    safe_box=SubtitleSafeBoxConfig(play_res_x=1080, center_x=540, side_padding_px=0),
                )
                dialogues = [
                    line.rsplit(",", 1)[-1]
                    for line in out_path.read_text(encoding="utf-8").splitlines()
                    if line.startswith("Dialogue:")
                ]

            self.assertEqual(len(dialogues), len(expected), text)
            for actual, expected_phrase in zip(dialogues, expected):
                self.assertIn(expected_phrase, actual.replace(r"\N", ""))

    def test_viral_pop_style_emits_pop_animation_and_ghost_layer(self) -> None:
        transcript = Transcript(
            words=[
                Word(word="уникальной", start=0.00, end=0.30),
                Word(word="информацией", start=0.34, end=0.70),
            ],
            full_text="уникальной информацией",
            duration=0.70,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "subs.ass"
            transcript_to_ass(
                transcript,
                _style(
                    font="Gilroy",
                    size=82,
                    accent_font="Bebas Neue Cyrillic",
                    accent_size=132,
                    accent_color="&H0000F5FF",
                    accent_outline=4,
                    animation="pop",
                    pop_enter_ms=110,
                    pop_settle_ms=80,
                    pop_start_scale=82,
                    pop_overshoot_scale=118,
                    pop_final_scale=100,
                    ghost_preflash=True,
                    ghost_alpha=110,
                    ghost_offset_px=2,
                    accent_keywords=["уникальной"],
                    accent_long_word_min_chars=9,
                ),
                out_path,
                safe_box=SubtitleSafeBoxConfig(play_res_x=1080, center_x=540, top_padding_px=260),
            )
            ass = out_path.read_text(encoding="utf-8")

        self.assertEqual(ass.count("Dialogue:"), 4)
        self.assertIn(r"Dialogue: 0", ass)
        self.assertIn(r"Dialogue: 1", ass)
        # Body captions are bottom-anchored by default (an2, just above the bottom safe pad).
        self.assertIn(r"\an2\pos(540,1460)", ass)
        self.assertIn(r"\fnBebas Neue Cyrillic", ass)
        self.assertIn(r"\1c&H0000F5FF", ass)
        self.assertIn(r"\alpha&HFF&", ass)
        self.assertIn(r"\t(0,110,\alpha&H00&\fscx118\fscy118)", ass)
        self.assertIn(r"\t(110,190,\fscx100\fscy100)", ass)

    def test_editorial_pop_groups_tokens_and_keeps_them_until_group_end(self) -> None:
        transcript = Transcript(
            words=[
                Word(word="раньше", start=0.00, end=0.22),
                Word(word="эксперты", start=0.26, end=0.62),
            ],
            full_text="раньше эксперты",
            duration=0.62,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "subs.ass"
            plan_path = Path(tmp_dir) / "caption_plan.json"
            transcript_to_ass(
                transcript,
                _style(
                    font="Gilroy",
                    size=74,
                    accent_font="Bebas Neue Cyrillic",
                    accent_size=128,
                    accent_color="&H0000F5FF",
                    accent_outline=4,
                    caption_mode="editorial",
                    animation="pop",
                    max_words_per_chunk=4,
                    max_chunk_duration_sec=1.4,
                    max_chunk_gap_sec=0.25,
                    forbid_two_content_words=False,
                    accent_keywords=["эксперты"],
                    ghost_preflash=False,
                ),
                out_path,
                safe_box=SubtitleSafeBoxConfig(play_res_x=1080, center_x=540, top_padding_px=260),
                caption_plan_path=plan_path,
                section="hook",
            )
            ass = out_path.read_text(encoding="utf-8")
            plan = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertEqual(len(plan["groups"]), 1)
        self.assertEqual(plan["groups"][0]["template"], "small_over_big")
        self.assertEqual(plan["groups"][0]["zone"], "bottom_third_center")
        self.assertEqual([token["role"] for token in plan["groups"][0]["tokens"]], ["support", "accent"])
        self.assertEqual([token["y"] for token in plan["groups"][0]["tokens"]], [1556, 1634])
        dialogue_lines = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        self.assertEqual(len(dialogue_lines), 2)
        first = dialogue_lines[0].split(",", 9)
        second = dialogue_lines[1].split(",", 9)
        self.assertNotEqual(first[1], second[1])
        self.assertEqual(first[2], second[2])
        self.assertIn(r"\1c&H0000F5FF", dialogue_lines[1])

    def test_editorial_pop_renders_adjacent_support_words_as_phrase(self) -> None:
        transcript = Transcript(
            words=[
                Word(word="по", start=0.00, end=0.10),
                Word(word="мнению", start=0.14, end=0.38),
                Word(word="рекрутеров", start=0.48, end=0.88),
            ],
            full_text="по мнению рекрутеров",
            duration=0.88,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "subs.ass"
            plan_path = Path(tmp_dir) / "caption_plan.json"
            transcript_to_ass(
                transcript,
                _style(
                    font="Gilroy",
                    size=74,
                    accent_font="Bebas Neue Cyrillic",
                    accent_size=128,
                    accent_color="&H0000F5FF",
                    accent_outline=4,
                    caption_mode="editorial",
                    animation="pop",
                    max_words_per_chunk=4,
                    max_chunk_duration_sec=1.4,
                    max_chunk_gap_sec=0.25,
                    forbid_two_content_words=False,
                    accent_keywords=["рекрутеров"],
                    ghost_preflash=False,
                ),
                out_path,
                safe_box=SubtitleSafeBoxConfig(play_res_x=1080, center_x=540, top_padding_px=260),
                caption_plan_path=plan_path,
                section="hook",
            )
            ass = out_path.read_text(encoding="utf-8")
            plan = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertEqual(
            [token["text"] for token in plan["groups"][0]["tokens"]],
            ["ПО МНЕНИЮ", "РЕКРУТЕРОВ"],
        )
        self.assertIn("ПО МНЕНИЮ", ass)

    def test_editorial_pop_does_not_accent_every_long_word(self) -> None:
        transcript = Transcript(
            words=[
                Word(word="проанализировал", start=0.00, end=0.40),
                Word(word="документ", start=0.44, end=0.78),
            ],
            full_text="проанализировал документ",
            duration=0.78,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "subs.ass"
            plan_path = Path(tmp_dir) / "caption_plan.json"
            transcript_to_ass(
                transcript,
                _style(
                    font="Gilroy",
                    size=74,
                    accent_font="Bebas Neue Cyrillic",
                    accent_size=128,
                    accent_color="&H0000F5FF",
                    caption_mode="editorial",
                    max_words_per_chunk=4,
                    max_chunk_duration_sec=1.4,
                    max_chunk_gap_sec=0.25,
                    forbid_two_content_words=False,
                    accent_long_word_min_chars=0,
                    ghost_preflash=False,
                ),
                out_path,
                safe_box=SubtitleSafeBoxConfig(play_res_x=1080, center_x=540, top_padding_px=260),
                caption_plan_path=plan_path,
            )
            plan = json.loads(plan_path.read_text(encoding="utf-8"))

        roles = [token["role"] for group in plan["groups"] for token in group["tokens"]]
        self.assertEqual(roles.count("accent"), 1)
        self.assertEqual(plan["groups"][0]["tokens"][-1]["text"], "ДОКУМЕНТ")

    def test_editorial_pop_splits_late_accent_to_avoid_white_only_lead(self) -> None:
        transcript = Transcript(
            words=[
                Word(word="HR", start=0.00, end=0.18),
                Word(word="тратит", start=0.22, end=0.44),
                Word(word="8", start=0.58, end=0.72),
            ],
            full_text="HR тратит 8",
            duration=0.72,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "subs.ass"
            plan_path = Path(tmp_dir) / "caption_plan.json"
            transcript_to_ass(
                transcript,
                _style(
                    font="Gilroy",
                    size=74,
                    accent_font="Bebas Neue Cyrillic",
                    accent_size=128,
                    accent_color="&H0000F5FF",
                    caption_mode="editorial",
                    max_words_per_chunk=4,
                    max_chunk_duration_sec=1.4,
                    max_chunk_gap_sec=0.25,
                    forbid_two_content_words=False,
                    accent_numbers=True,
                    ghost_preflash=False,
                ),
                out_path,
                safe_box=SubtitleSafeBoxConfig(play_res_x=1080, center_x=540, top_padding_px=260),
                caption_plan_path=plan_path,
                section="hook",
            )
            plan = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertEqual(len(plan["groups"]), 2)
        self.assertEqual([token["role"] for token in plan["groups"][0]["tokens"]], ["support", "accent"])
        self.assertEqual(plan["groups"][0]["tokens"][-1]["text"], "ТРАТИТ")
        self.assertEqual(plan["groups"][1]["tokens"][0]["role"], "accent")

    def test_editorial_pop_keeps_first_accent_large_in_multiword_group(self) -> None:
        transcript = Transcript(
            words=[
                Word(word="8", start=0.00, end=0.14),
                Word(word="секунд", start=0.16, end=0.40),
                Word(word="на", start=0.42, end=0.50),
            ],
            full_text="8 секунд на",
            duration=0.50,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "subs.ass"
            plan_path = Path(tmp_dir) / "caption_plan.json"
            transcript_to_ass(
                transcript,
                _style(
                    font="Gilroy",
                    size=74,
                    accent_font="Bebas Neue Cyrillic",
                    accent_size=128,
                    accent_color="&H0000F5FF",
                    caption_mode="editorial",
                    max_words_per_chunk=4,
                    max_chunk_duration_sec=1.4,
                    max_chunk_gap_sec=0.25,
                    forbid_two_content_words=False,
                    accent_numbers=True,
                    ghost_preflash=False,
                ),
                out_path,
                safe_box=SubtitleSafeBoxConfig(play_res_x=1080, center_x=540, top_padding_px=260),
                caption_plan_path=plan_path,
                section="hook",
            )
            plan = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertEqual(plan["groups"][0]["template"], "big_over_small")
        self.assertEqual(plan["groups"][0]["tokens"][0]["text"], "8")
        self.assertEqual(plan["groups"][0]["tokens"][0]["role"], "accent")
        self.assertEqual(plan["groups"][0]["tokens"][0]["size"], "huge")

    def test_editorial_pop_moves_trailing_helper_to_next_group(self) -> None:
        transcript = Transcript(
            words=[
                Word(word="чего", start=0.00, end=0.20),
                Word(word="достиг", start=0.24, end=0.52),
                Word(word="в", start=0.56, end=0.62),
                Word(word="цифрах", start=0.66, end=0.96),
            ],
            full_text="чего достиг в цифрах",
            duration=0.96,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "subs.ass"
            plan_path = Path(tmp_dir) / "caption_plan.json"
            transcript_to_ass(
                transcript,
                _style(
                    font="Gilroy",
                    size=74,
                    accent_font="Bebas Neue Cyrillic",
                    accent_size=128,
                    accent_color="&H0000F5FF",
                    caption_mode="editorial",
                    max_words_per_chunk=4,
                    max_chunk_duration_sec=1.4,
                    max_chunk_gap_sec=0.25,
                    forbid_two_content_words=False,
                    ghost_preflash=False,
                ),
                out_path,
                safe_box=SubtitleSafeBoxConfig(play_res_x=1080, center_x=540, top_padding_px=260),
                caption_plan_path=plan_path,
            )
            plan = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertEqual([token["text"] for token in plan["groups"][0]["tokens"]], ["ЧЕГО", "ДОСТИГ"])
        self.assertEqual([token["text"] for token in plan["groups"][1]["tokens"]], ["В ЦИФРАХ"])
        self.assertNotEqual(plan["groups"][0]["tokens"][-1]["text"], "В")

    def test_editorial_pop_attaches_short_helper_to_following_accent(self) -> None:
        transcript = Transcript(
            words=[
                Word(word="воронку", start=0.00, end=0.28),
                Word(word="и", start=0.32, end=0.38),
                Word(word="поднял", start=0.42, end=0.72),
            ],
            full_text="воронку и поднял",
            duration=0.72,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "subs.ass"
            plan_path = Path(tmp_dir) / "caption_plan.json"
            transcript_to_ass(
                transcript,
                _style(
                    font="Gilroy",
                    size=74,
                    accent_font="Bebas Neue Cyrillic",
                    accent_size=128,
                    accent_color="&H0000F5FF",
                    caption_mode="editorial",
                    max_words_per_chunk=4,
                    max_chunk_duration_sec=1.4,
                    max_chunk_gap_sec=0.25,
                    forbid_two_content_words=False,
                    ghost_preflash=False,
                ),
                out_path,
                safe_box=SubtitleSafeBoxConfig(play_res_x=1080, center_x=540, top_padding_px=260),
                caption_plan_path=plan_path,
            )
            ass = out_path.read_text(encoding="utf-8")
            plan = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertEqual(
            [token["text"] for token in plan["groups"][0]["tokens"]],
            ["ВОРОНКУ", "И ПОДНЯЛ"],
        )
        self.assertIn("И ПОДНЯЛ", ass)

    def test_editorial_pop_middle_accent_does_not_make_last_helper_huge(self) -> None:
        transcript = Transcript(
            words=[
                Word(word="чего", start=0.00, end=0.20),
                Word(word="достиг", start=0.24, end=0.52),
                Word(word="в", start=0.56, end=0.62),
            ],
            full_text="чего достиг в",
            duration=0.62,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "subs.ass"
            plan_path = Path(tmp_dir) / "caption_plan.json"
            transcript_to_ass(
                transcript,
                _style(
                    font="Gilroy",
                    size=74,
                    accent_font="Bebas Neue Cyrillic",
                    accent_size=128,
                    accent_color="&H0000F5FF",
                    caption_mode="editorial",
                    max_words_per_chunk=4,
                    max_chunk_duration_sec=1.4,
                    max_chunk_gap_sec=0.25,
                    forbid_two_content_words=False,
                    accent_keywords=["достиг"],
                    ghost_preflash=False,
                ),
                out_path,
                safe_box=SubtitleSafeBoxConfig(play_res_x=1080, center_x=540, top_padding_px=260),
                caption_plan_path=plan_path,
            )
            plan = json.loads(plan_path.read_text(encoding="utf-8"))

        tokens = plan["groups"][0]["tokens"]
        self.assertEqual(tokens[1]["text"], "ДОСТИГ")
        self.assertEqual(tokens[1]["role"], "accent")
        self.assertEqual(tokens[1]["size"], "huge")
        self.assertEqual(tokens[2]["text"], "В")
        self.assertEqual(tokens[2]["role"], "support")
        self.assertEqual(tokens[2]["size"], "small")

    def test_hook_cta_transcription_uses_configured_whisper_model(self) -> None:
        raw_config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        cfg = Config(**raw_config)
        script = _load_script(ROOT / "scripts/sample-hook-cta.json")

        calls: list[tuple[str, str]] = []

        def fake_transcribe_asset(path, *, model_size="medium", language="ru"):
            calls.append((path.name, model_size))
            return Transcript(
                words=[Word(word="тест", start=0.0, end=0.4)],
                full_text="тест",
                duration=0.4,
            )

        def fake_prepare_voiceover_assets(*args, **kwargs):
            return (
                Path("voiceover.mp3"),
                Transcript(
                    words=[Word(word="тест", start=0.0, end=0.4)],
                    full_text="тест",
                    duration=0.4,
                ),
                0.0,
                None,
                VoiceoverTextPlan(
                    tts_text="тест",
                    align_text="тест",
                    display_text="тест",
                ),
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = cfg.model_copy(update={"output_dir": tmp_dir})
            with (
                patch("src.cli.validate_assets"),
                patch("src.cli.get_asset_path", side_effect=lambda asset_type, asset_id: Path(f"{asset_id}.mp4")),
                patch("src.cli.transcribe_asset", side_effect=fake_transcribe_asset),
                patch("src.cli.render_hook_montage") as render_hook_montage,
                patch("src.cli._prepare_voiceover_assets", side_effect=fake_prepare_voiceover_assets),
                patch("src.cli.transcript_to_ass"),
                patch("src.cli.plan_broll") as plan_broll,
                patch("src.cli.build_block_plan", side_effect=lambda render_plan, _cfg: render_plan),
                patch("src.cli.write_render_diagnostics"),
                patch("src.cli.build_broll_section", return_value=Path("broll_section.mp4")),
                patch("src.cli.concat_final", return_value=Path("final.mp4")) as concat_final,
                patch(
                    "src.cli.inspect_first_frames",
                    return_value=type(
                        "FirstFrameReportStub",
                        (),
                        {
                            "is_black": False,
                            "mean_luma": 100.0,
                            "black_pixel_ratio": 0.0,
                        },
                    )(),
                ),
                patch("src.cli._append_hook_cta_duration_warnings"),
                patch("src.cli._resolve_music_path", return_value=Path("music.mp3")),
            ):
                from src.schemas import RenderPlan

                render_hook_montage.return_value = type(
                    "HookMontageResultStub",
                    (),
                    {
                        "video_path": Path("hook_montage.mp4"),
                        "subtitle_path": Path("hook_montage_subs.ass"),
                        "transcript": Transcript(
                            words=[Word(word="тест", start=0.0, end=0.4)],
                            full_text="тест",
                            duration=0.4,
                        ),
                        "diagnostics": HookMontageDiagnostics(enabled=False),
                        "subtitle_warnings": [],
                    },
                )()
                plan_broll.return_value = RenderPlan(
                    strategy="auto",
                    target_duration_sec=0.4,
                    planned_duration_sec=0.4,
                    soft_rules=False,
                    clips=[],
                )
                _run_build(script, cfg)

        self.assertEqual(concat_final.call_args.kwargs["music_start_sec"], 0.0)
        self.assertEqual(
            calls,
            [
                ("hook_4.mp4", "large-v3"),
                ("cta_4.mp4", "large-v3"),
            ],
        )

    def test_run_build_stops_before_broll_when_hook_guardrail_fails(self) -> None:
        raw_config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        cfg = Config(**raw_config).model_copy(update={"output_dir": tempfile.mkdtemp()})
        script = _load_script(ROOT / "scripts/2026-05-20-ролик-12-охват-сохранить.json")

        transcript = Transcript(
            words=[Word(word="тест", start=0.0, end=0.4)],
            full_text="тест",
            duration=0.4,
        )
        hook_result = type(
            "HookMontageResultStub",
            (),
            {
                "video_path": Path("hook_montage.mp4"),
                "subtitle_path": Path("hook_montage_subs.ass"),
                "transcript": Transcript(
                    words=[Word(word="достижений", start=0.0, end=0.5)],
                    full_text="достижений",
                    duration=0.5,
                ),
                "diagnostics": HookMontageDiagnostics(
                    enabled=True,
                    final_duration_sec=10.17,
                    warnings=["hook montage longer than target_max_sec: 10.17s"],
                ),
                "subtitle_warnings": [],
            },
        )()
        asset_meta = {
            "hooks": {
                script.hook_id: AssetEntry(
                    file=f"{script.hook_id}.mp4",
                    duration=1.0,
                    transcript_text="тест",
                )
            },
            "ctas": {
                script.cta_id: AssetEntry(
                    file=f"{script.cta_id}.mp4",
                    duration=1.0,
                    transcript_text="тест",
                )
            },
        }

        with (
            patch("src.cli.validate_assets"),
            patch("src.cli.get_asset_path", side_effect=lambda asset_type, asset_id: Path(f"{asset_id}.mp4")),
            patch("src.cli.load_asset_meta", side_effect=lambda asset_type: asset_meta[asset_type]),
            patch("src.cli.transcribe_asset", return_value=transcript),
            patch("src.cli.render_hook_montage", return_value=hook_result),
            patch(
                "src.cli._prepare_voiceover_assets",
                return_value=(
                    Path("voiceover.mp3"),
                    transcript,
                    0.0,
                    None,
                    VoiceoverTextPlan(
                        tts_text="тест",
                        align_text="тест",
                        display_text="тест",
                    ),
                ),
            ),
            patch("src.cli.transcript_to_ass"),
            patch("src.cli.plan_broll") as plan_broll,
        ):
            plan_broll.side_effect = AssertionError("plan_broll should not be called")
            with self.assertRaisesRegex(RuntimeError, "hook montage is 10.17s"):
                _run_build(script, cfg)

        plan_broll.assert_not_called()

    def test_run_build_allows_raw_long_cta_when_normalized_cta_fits(self) -> None:
        raw_config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        cfg = Config(**raw_config)
        script = _load_script(ROOT / "scripts/sample-hook-cta.json")

        body_transcript = Transcript(
            words=[Word(word="тест", start=0.0, end=0.4)],
            full_text="тест",
            duration=0.4,
        )
        raw_long_cta = Transcript(
            words=[Word(word="проверь", start=0.4, end=0.8)],
            full_text="проверь",
            duration=8.0,
        )

        def fake_transcribe_asset(path, **kwargs):
            if path.name.startswith("cta"):
                return raw_long_cta
            return body_transcript

        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = cfg.model_copy(update={"output_dir": tmp_dir})
            with (
                patch("src.cli.validate_assets"),
                patch("src.cli.get_asset_path", side_effect=lambda asset_type, asset_id: Path(f"{asset_id}.mp4")),
                patch("src.cli.transcribe_asset", side_effect=fake_transcribe_asset),
                patch("src.cli.render_hook_montage") as render_hook_montage,
                patch(
                    "src.cli._prepare_voiceover_assets",
                    return_value=(
                        Path("voiceover.mp3"),
                        body_transcript,
                        0.0,
                        None,
                        VoiceoverTextPlan(
                            tts_text="тест",
                            align_text="тест",
                            display_text="тест",
                        ),
                    ),
                ),
                patch("src.cli._normalized_cta_duration_sec", return_value=5.5),
                patch("src.cli.transcript_to_ass"),
                patch("src.cli.plan_broll") as plan_broll,
                patch("src.cli.build_block_plan", side_effect=lambda render_plan, _cfg: render_plan),
                patch("src.cli.write_render_diagnostics"),
                patch("src.cli.build_broll_section", return_value=Path("broll_section.mp4")),
                patch("src.cli.concat_final", return_value=Path("final.mp4")),
                patch(
                    "src.cli.inspect_first_frames",
                    return_value=type(
                        "FirstFrameReportStub",
                        (),
                        {
                            "is_black": False,
                            "mean_luma": 100.0,
                            "black_pixel_ratio": 0.0,
                        },
                    )(),
                ),
                patch("src.cli._append_hook_cta_duration_warnings"),
                patch("src.cli._resolve_music_path", return_value=None),
            ):
                from src.schemas import RenderPlan

                render_hook_montage.return_value = type(
                    "HookMontageResultStub",
                    (),
                    {
                        "video_path": Path("hook_montage.mp4"),
                        "subtitle_path": Path("hook_montage_subs.ass"),
                        "transcript": body_transcript,
                        "diagnostics": HookMontageDiagnostics(enabled=False),
                        "subtitle_warnings": [],
                    },
                )()
                plan_broll.return_value = RenderPlan(
                    strategy="auto",
                    target_duration_sec=0.4,
                    planned_duration_sec=0.4,
                    soft_rules=False,
                    clips=[],
                )

                _run_build(script, cfg)

        plan_broll.assert_called_once()


if __name__ == "__main__":
    unittest.main()
