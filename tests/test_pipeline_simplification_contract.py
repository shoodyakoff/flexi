from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from src.cli import _output_slug, _resolve_build_config
from src.schemas import Config, VideoScript

ROOT = Path(__file__).resolve().parents[1]


class PipelineSimplificationContractTest(unittest.TestCase):
    def test_config_uses_single_simplified_edit_profile(self) -> None:
        raw_config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        cfg = Config(**raw_config)

        self.assertNotIn("mode_profiles", raw_config)
        self.assertEqual(cfg.edit_profile.subtitle_style, "editorial_pop")
        self.assertEqual(cfg.edit_profile.rhythm_profile, "provocative_soft")
        self.assertEqual(cfg.edit_profile.look_profile, "punchy")
        self.assertEqual(cfg.audio.voiceover_tail_silence_sec, 0.1)
        self.assertEqual(cfg.whisper.align_model, "large-v3")
        self.assertEqual(cfg.whisper.transcribe_model, "large-v3")
        self.assertEqual(cfg.tts.generation_mode, "direct")
        self.assertEqual(cfg.tts.markup_dialect, "v2")
        self.assertEqual(cfg.tts.output_format, "mp3_44100_192")
        self.assertEqual(cfg.tts.apply_text_normalization, "auto")
        self.assertEqual(cfg.tts.model_id, "eleven_multilingual_v2")
        self.assertEqual(cfg.subtitle_styles["bold_highlight"].font, "Onest")
        self.assertEqual(cfg.subtitle_styles["bold_highlight"].size, 85)
        self.assertEqual(cfg.subtitle_styles["bold_highlight"].outline_color, "&H66000000")
        self.assertEqual(cfg.subtitle_styles["bold_highlight"].outline, 3)
        self.assertEqual(cfg.subtitle_styles["bold_highlight"].shadow, 1)
        self.assertEqual(len(cfg.tts.pronunciation_dictionary_locators), 0)
        self.assertNotIn("stability", raw_config["tts"])
        self.assertNotIn("style", raw_config["tts"])
        self.assertNotIn("seed", raw_config["tts"])
        self.assertNotIn("pronunciation_dictionary_path", raw_config["tts"])
        self.assertNotIn("slow_speed_factor", raw_config["tts"])
        self.assertNotIn("final_sentence_speed_factor", raw_config["tts"])
        self.assertNotIn("sentence_pause_sec", raw_config["audio"])
        self.assertEqual(cfg.subtitle_styles["bold_highlight"].max_words_per_chunk, 1)
        self.assertEqual(cfg.subtitle_styles["bold_highlight"].max_lines, 2)
        self.assertTrue(cfg.subtitle_styles["bold_highlight"].split_long_words)
        self.assertTrue(cfg.subtitle_styles["bold_highlight"].attach_short_tokens)
        self.assertTrue(cfg.subtitle_styles["bold_highlight"].attach_numbers)
        self.assertTrue(cfg.subtitle_styles["bold_highlight"].forbid_two_content_words)
        viral_style = cfg.subtitle_styles["viral_pop_bebas"]
        self.assertEqual(viral_style.font, "Onest")
        self.assertEqual(viral_style.accent_font, "Bebas Neue Cyrillic")
        self.assertEqual(viral_style.animation, "pop")
        self.assertTrue(viral_style.ghost_preflash)
        self.assertTrue(viral_style.accent_numbers)
        editorial_style = cfg.subtitle_styles["editorial_pop"]
        self.assertEqual(editorial_style.caption_mode, "editorial")
        self.assertEqual(editorial_style.max_words_per_chunk, 4)
        self.assertFalse(editorial_style.forbid_two_content_words)
        self.assertEqual(editorial_style.accent_long_word_min_chars, 0)

    def test_config_has_render_guardrails(self) -> None:
        raw_config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        cfg = Config(**raw_config)

        self.assertTrue(cfg.render_guardrails.fail_on_long_hook)
        self.assertTrue(cfg.render_guardrails.fail_on_missing_hook_accent)
        self.assertAlmostEqual(cfg.render_guardrails.cta_target_max_sec, 6.0)
        self.assertEqual(cfg.audio.music_start_mode, "full_reel")
        self.assertEqual(cfg.broll_rotation.recent_start_penalty, 18.0)

    def test_config_has_no_duplicate_top_level_or_nested_keys(self) -> None:
        text = (ROOT / "config.yaml").read_text(encoding="utf-8")
        stack: list[tuple[int, set[str]]] = [(-1, set())]

        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].rstrip()
            if not line.strip() or line.lstrip().startswith("-"):
                continue
            indent = len(raw_line) - len(raw_line.lstrip(" "))
            stripped = line.strip()
            if ":" not in stripped:
                continue
            key = stripped.split(":", 1)[0].strip().strip("\"'")
            while stack and indent <= stack[-1][0]:
                stack.pop()
            if key in stack[-1][1]:
                self.fail(f"duplicate YAML key at indent {indent}: {key}")
            stack[-1][1].add(key)
            if stripped.endswith(":"):
                stack.append((indent, set()))

    def test_main_build_path_does_not_call_parked_effect_systems(self) -> None:
        cli_source = (ROOT / "src" / "cli.py").read_text(encoding="utf-8")

        self.assertNotIn("build_motion_plan", cli_source)
        self.assertNotIn("build_transition_plan", cli_source)
        self.assertNotIn("build_sfx_plan", cli_source)
        self.assertNotIn("build_beat_plan", cli_source)

    def test_runtime_code_has_no_ordinary_mode_branch(self) -> None:
        runtime_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "src").glob("*.py")
        )

        self.assertNotIn("ordinary", runtime_source.lower())

    def test_tts_preset_switches_model_and_dialect_for_ab_runs(self) -> None:
        raw_config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        cfg = Config(**raw_config)
        script = VideoScript(
            slug="sample",
            hook_id="hook_6",
            cta_id="cta_6",
            voiceover_text="Текст",
        )

        v2_cfg, v2_preset = _resolve_build_config(cfg, script, tts_preset="v2")
        v3_cfg, v3_preset = _resolve_build_config(cfg, script, tts_preset="v3")

        self.assertEqual(v2_preset, "v2")
        self.assertEqual(v2_cfg.tts.model_id, "eleven_multilingual_v2")
        self.assertEqual(v2_cfg.tts.markup_dialect, "v2")
        self.assertEqual(v3_preset, "v3")
        self.assertEqual(v3_cfg.tts.model_id, "eleven_v3")
        self.assertEqual(v3_cfg.tts.markup_dialect, "v3")
        self.assertEqual(_output_slug(script.slug, v3_preset), "sample-v3")

    def test_numbered_reel_output_slug_uses_only_reel_number(self) -> None:
        self.assertEqual(
            _output_slug("reel-011-intro-save", "v2"),
            "reel-011",
        )
        self.assertEqual(
            _output_slug("2026-05-07-reel-004-v1", "v2"),
            "reel-004",
        )


if __name__ == "__main__":
    unittest.main()
