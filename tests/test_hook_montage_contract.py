from __future__ import annotations

import unittest
import tempfile
import subprocess
from pathlib import Path
from unittest.mock import patch

import yaml

from src.hook_montage import (
    _hook_motion_filter,
    _plan_render_duration_sec,
    build_hook_montage_plan,
    write_hook_montage_ass,
)
from src.cli import _enforce_render_guardrails
from src.assembly import (
    _alternating_broll_motion_preset,
    _apply_hook_flash_to_hook_tail,
    _center_zoompan_chain,
    _cta_duration_limit_sec,
    _concat_copy,
    _cta_lead_trim_sec,
    _normalize_cta_clip,
    _cta_punch_zoompan_chain,
    _select_cta_punch_cue,
    _should_flash_broll_cut,
    _broll_transitions_enabled,
    _XFADE_NAME_BY_KIND,
    add_background_music,
)
from src.schemas import Config, HookMontageDiagnostics, Transcript, Word

ROOT = Path(__file__).resolve().parents[1]


class HookMontageContractTest(unittest.TestCase):
    def _config(self) -> Config:
        raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        return Config(**raw)

    def test_keyword_overlay_and_riser_peak_sync(self) -> None:
        cfg = self._config()
        transcript = Transcript(
            words=[
                Word(word="получил", start=1.00, end=1.35),
                Word(word="оффер", start=2.20, end=2.55),
                Word(word="быстрее", start=3.00, end=3.45),
                Word(word="дальше", start=5.80, end=6.40),
            ],
            full_text="получил оффер быстрее дальше",
            duration=6.40,
        )

        with (
            patch("src.hook_montage._probe_duration", return_value=8.0),
            patch("src.hook_montage._probe_media_duration", return_value=2.115875),
        ):
            plan = build_hook_montage_plan(Path("assets/hooks/hook_4.mp4"), transcript, cfg)

        self.assertGreaterEqual(len(plan.kept_ranges), 2)
        self.assertGreaterEqual(len(plan.removed_ranges), 2)
        self.assertEqual(len(plan.overlays), 1)
        overlay = plan.overlays[0]
        self.assertEqual(overlay.keyword, "оффер")
        self.assertEqual(overlay.file, ROOT / "assets/hook_overlays/money.png")
        self.assertEqual(overlay.placement, "bottom_right")
        self.assertGreater(overlay.peak_sec, overlay.word_start_sec)
        self.assertLessEqual(overlay.peak_sec, overlay.word_end_sec)
        self.assertAlmostEqual(overlay.end_sec - overlay.start_sec, 2.0, places=3)

        self.assertGreaterEqual(len(plan.sfx_events), 1)
        riser = plan.sfx_events[0]
        self.assertEqual(riser.kind, "riser")
        self.assertEqual(riser.gain_db, -8.5)
        self.assertEqual(riser.peak_target_sec, overlay.peak_sec)
        self.assertEqual(riser.timeline_start_sec, 0.0)
        self.assertGreater(riser.trim_start_sec, 0.0)
        self.assertTrue(
            any(event.startswith("riser_zoom_out_in:@") for event in plan.motion_events)
        )
        self.assertEqual(cfg.hook_montage.motion.riser_zoom_delta, 0.28)
        self.assertEqual(cfg.hook_montage.motion.riser_zoom_out_duration_sec, 9.0)
        self.assertEqual(cfg.hook_montage.motion.riser_zoom_in_duration_sec, 0.40)

        style = cfg.subtitle_styles[cfg.hook_montage.typography.style or cfg.edit_profile.subtitle_style]
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "hook.ass"
            write_hook_montage_ass(
                plan.transcript,
                style,
                out_path,
                cfg=cfg,
                accent_cues=plan.accent_cues,
            )
            ass = out_path.read_text(encoding="utf-8")

        self.assertIn(r"\pos(540,255)", ass)
        self.assertNotIn(r"\pos(540,470)", ass)
        self.assertIn(r"\fs112", ass)

    def test_hook_without_anchor_or_accent_words_skips_overlay_and_riser(self) -> None:
        cfg = self._config()
        transcript = Transcript(
            words=[
                Word(word="сверху,", start=4.80, end=5.10),
                Word(word="объясняю,", start=5.20, end=5.82),
                Word(word="что", start=5.90, end=6.08),
            ],
            full_text="сверху объясняю что",
            duration=6.08,
        )

        with (
            patch("src.hook_montage._probe_duration", return_value=7.74),
            patch("src.hook_montage._probe_media_duration", return_value=2.115875),
        ):
            plan = build_hook_montage_plan(Path("assets/hooks/hook_6.mov"), transcript, cfg)

        # No anchor words from the icon library appear in this hook → no overlay
        # and no riser SFX (which is anchored to the overlay's peak).
        self.assertEqual(len(plan.overlays), 0)
        self.assertEqual(plan.sfx_events, [])
        self.assertFalse(
            any(event.startswith("riser_zoom_out_in:@") for event in plan.motion_events)
        )

    def test_long_hook_preserves_natural_pause_and_tail_padding(self) -> None:
        cfg = self._config()
        transcript = Transcript(
            words=[
                Word(word="По", start=2.44, end=3.04),
                Word(word="мнению", start=3.06, end=3.48),
                Word(word="уважаемых", start=3.48, end=4.32),
                Word(word="рекрутеров,", start=4.32, end=5.10),
                Word(word="а", start=5.12, end=5.24),
                Word(word="они", start=5.26, end=5.42),
                Word(word="имеются,", start=5.42, end=6.00),
                Word(word="всего", start=6.52, end=6.62),
                Word(word="одно", start=6.62, end=6.90),
                Word(word="слово", start=6.90, end=7.28),
                Word(word="в", start=7.28, end=7.48),
                Word(word="резюме", start=7.48, end=7.76),
                Word(word="делает", start=7.76, end=8.02),
                Word(word="его", start=8.02, end=8.30),
                Word(word="слабым,", start=8.30, end=8.86),
                Word(word="и", start=8.86, end=8.96),
                Word(word="это", start=8.98, end=9.08),
                Word(word="слово", start=9.08, end=9.40),
                Word(word="занимался.", start=9.40, end=10.10),
            ],
            full_text=(
                "По мнению уважаемых рекрутеров, а они имеются, всего одно слово "
                "в резюме делает его слабым, и это слово занимался."
            ),
            duration=10.10,
        )

        with (
            patch("src.hook_montage._probe_duration", return_value=14.683),
            patch("src.hook_montage._probe_media_duration", return_value=2.115875),
        ):
            plan = build_hook_montage_plan(Path("assets/hooks/hook_11.mov"), transcript, cfg)

        self.assertLess(cfg.hook_montage.speech_lead_padding_sec, cfg.hook_montage.speech_padding_sec)
        self.assertEqual(len(plan.kept_ranges), 1)
        self.assertGreaterEqual(plan.kept_ranges[0][0], 2.30)
        self.assertGreaterEqual(plan.kept_ranges[0][1], 10.45)
        self.assertLessEqual(_plan_render_duration_sec(plan, cfg), cfg.hook_montage.target_max_sec)
        self.assertAlmostEqual(_plan_render_duration_sec(plan, cfg), plan.final_duration_sec, places=3)
        self.assertIn("имеются,", [word.word for word in plan.transcript.words])
        self.assertEqual(plan.warnings, [])

    def test_hook_montage_trims_detected_audio_lead_silence(self) -> None:
        cfg = self._config()
        transcript = Transcript(
            words=[
                Word(word="По", start=0.07, end=1.02),
                Word(word="мнению", start=1.04, end=1.34),
                Word(word="рекрутеров,", start=1.34, end=1.90),
                Word(word="опыт.", start=6.14, end=6.58),
            ],
            full_text="По мнению рекрутеров, опыт.",
            duration=6.58,
        )
        silence_probe = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr=(
                "[silencedetect @ 0x1] silence_start: 0\n"
                "[silencedetect @ 0x1] silence_end: 1.051354 | silence_duration: 1.051354\n"
            ),
        )

        with (
            patch("src.hook_montage._probe_duration", return_value=7.867),
            patch("src.hook_montage._probe_media_duration", return_value=2.115875),
            patch("src.hook_montage.subprocess.run", return_value=silence_probe),
        ):
            plan = build_hook_montage_plan(Path("assets/hooks/hook_11.mov"), transcript, cfg)

        self.assertGreaterEqual(plan.kept_ranges[0][0], 0.80)
        self.assertLess(plan.kept_ranges[0][0], 1.05)
        self.assertEqual(plan.transcript.words[0].word, "По")
        self.assertAlmostEqual(plan.transcript.words[0].start, 0.0, places=3)

    def test_hook_accent_keywords_create_text_fallback_without_overlay_anchor(self) -> None:
        cfg = self._config()
        cfg.hook_montage.typography.accent_keywords = ["достижений"]
        transcript = Transcript(
            words=[
                Word(word="вместо", start=0.00, end=0.30),
                Word(word="достижений", start=0.34, end=0.90),
                Word(word="список", start=1.00, end=1.40),
            ],
            full_text="вместо достижений список",
            duration=1.40,
        )

        with (
            patch("src.hook_montage._probe_duration", return_value=2.0),
            patch("src.hook_montage._probe_media_duration", return_value=2.115875),
        ):
            plan = build_hook_montage_plan(Path("assets/hooks/hook_22.mov"), transcript, cfg)

        self.assertEqual(len(plan.accent_cues), 1)
        self.assertEqual(plan.accent_cues[0].keyword, "достижений")
        self.assertEqual(plan.accent_cues[0].word, "достижений")
        self.assertEqual(plan.overlays, [])
        self.assertTrue(any(event.kind == "riser" for event in plan.sfx_events))
        self.assertTrue(
            any(event.startswith("riser_zoom_out_in:@") for event in plan.motion_events)
        )

    def test_build_guardrails_reject_long_hook(self) -> None:
        from src.cli import _enforce_render_guardrails
        from src.schemas import HookMontageDiagnostics

        cfg = self._config()
        diagnostics = HookMontageDiagnostics(
            enabled=True,
            final_duration_sec=10.17,
            warnings=["hook montage longer than target_max_sec: 10.17s"],
        )

        with self.assertRaisesRegex(RuntimeError, "hook montage is 10.17s"):
            _enforce_render_guardrails(
                cfg,
                hook_montage=diagnostics,
                cta_duration_sec=4.0,
            )

    def test_build_guardrails_reject_missing_accent_when_only_opening_motion_exists(self) -> None:
        from src.cli import _enforce_render_guardrails
        from src.schemas import HookMontageDiagnostics

        cfg = self._config()
        diagnostics = HookMontageDiagnostics(
            enabled=True,
            final_duration_sec=4.0,
            motion_events=["opening_punch_in:0.55s:0.025"],
        )

        with self.assertRaisesRegex(RuntimeError, "hook has no accent effect"):
            _enforce_render_guardrails(
                cfg,
                hook_montage=diagnostics,
                cta_duration_sec=4.0,
            )

    def test_hook_render_uses_real_tail_padding_without_cloned_freeze(self) -> None:
        cfg = self._config()
        transcript = Transcript(
            words=[
                Word(word="сверху,", start=4.80, end=5.10),
                Word(word="показываю,", start=5.20, end=5.82),
                Word(word="что", start=5.90, end=6.08),
            ],
            full_text="сверху показываю что",
            duration=6.08,
        )

        with (
            patch("src.hook_montage._probe_duration", return_value=7.74),
            patch("src.hook_montage._probe_media_duration", return_value=2.115875),
        ):
            plan = build_hook_montage_plan(Path("assets/hooks/hook_6.mov"), transcript, cfg)

        self.assertGreater(cfg.hook_montage.tail_padding_sec, 0.0)
        self.assertAlmostEqual(
            _plan_render_duration_sec(plan, cfg),
            plan.final_duration_sec,
            places=3,
        )
        self.assertGreater(plan.final_duration_sec, plan.transcript.duration)
        for event in plan.sfx_events:
            self.assertLessEqual(
                event.timeline_start_sec + event.duration_sec,
                _plan_render_duration_sec(plan, cfg) + 0.001,
            )

    def test_accent_word_isolated_from_helper_tokens_in_hook_subtitles(self) -> None:
        cfg = self._config()
        transcript = Transcript(
            words=[
                Word(word="без", start=0.00, end=0.18),
                Word(word="оффера", start=0.20, end=0.56),
                Word(word="никуда", start=0.62, end=0.98),
            ],
            full_text="без оффера никуда",
            duration=0.98,
        )

        with (
            patch("src.hook_montage._probe_duration", return_value=1.2),
            patch("src.hook_montage._probe_media_duration", return_value=2.115875),
        ):
            plan = build_hook_montage_plan(Path("assets/hooks/hook_4.mp4"), transcript, cfg)

        style = cfg.subtitle_styles[cfg.hook_montage.typography.style or cfg.edit_profile.subtitle_style]
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "hook.ass"
            write_hook_montage_ass(
                plan.transcript,
                style,
                out_path,
                cfg=cfg,
                accent_cues=plan.accent_cues,
            )
            dialogue_lines = [
                line
                for line in out_path.read_text(encoding="utf-8").splitlines()
                if line.startswith("Dialogue:")
            ]

        self.assertEqual(len(dialogue_lines), 3)
        self.assertTrue(any(r"\pos(540,255)" in line and "ОФФЕРА" in line for line in dialogue_lines))
        self.assertTrue(any(r"\pos(540,255)" in line and "БЕЗ" in line for line in dialogue_lines))
        self.assertTrue(any(r"\pos(540,255)" in line and "НИКУДА" in line for line in dialogue_lines))

    def test_hook_sfx_gains_stay_audible(self) -> None:
        cfg = self._config()

        self.assertGreaterEqual(cfg.hook_montage.sfx.riser_gain_db, -12.0)
        self.assertGreaterEqual(cfg.hook_montage.sfx.swoosh_gain_db, -12.0)
        self.assertLessEqual(cfg.hook_montage.sfx.riser_gain_db, 0.0)
        self.assertLessEqual(cfg.hook_montage.sfx.swoosh_gain_db, 0.0)

    def test_hook_motion_filter_uses_center_locked_zoompan(self) -> None:
        cfg = self._config()
        transcript = Transcript(
            words=[
                Word(word="получить", start=4.12, end=4.32),
                Word(word="долгожданный", start=4.32, end=5.08),
                Word(word="оффер", start=5.08, end=5.62),
            ],
            full_text="получить долгожданный оффер",
            duration=5.62,
        )

        with (
            patch("src.hook_montage._probe_duration", return_value=8.3167),
            patch("src.hook_montage._probe_media_duration", return_value=2.115875),
        ):
            plan = build_hook_montage_plan(Path("assets/hooks/hook_5.mp4"), transcript, cfg)

        motion_filter = _hook_motion_filter(width=1080, height=1920, plan=plan, cfg=cfg)

        self.assertIsNotNone(motion_filter)
        assert motion_filter is not None
        self.assertIn("fps=30", motion_filter)
        self.assertIn("scale=2160:3840:flags=lanczos", motion_filter)
        self.assertIn("zoompan=z='1+(", motion_filter)
        self.assertIn("x='iw/2-(iw/zoom/2)'", motion_filter)
        self.assertIn("y='ih/2-(ih/zoom/2)'", motion_filter)
        self.assertIn("d=1:fps=30:s=2160x3840", motion_filter)

    def test_background_music_can_start_at_zero(self) -> None:
        cfg = self._config()
        cfg.audio.music_start_mode = "full_reel"

        with (
            patch("src.assembly._probe_duration", return_value=20.0),
            patch("src.assembly._run") as run_cmd,
        ):
            add_background_music(
                Path("final_clean.mp4"),
                Path("music.mp3"),
                Path("final_music.mp4"),
                cfg,
                start_sec=4.153,
            )

        cmd = run_cmd.call_args.args[0]
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertNotIn("adelay=", filter_complex)
        self.assertIn("afade=t=in:st=0.000:d=1.000", filter_complex)

    def test_background_music_can_start_after_hook_effect_for_legacy_mode(self) -> None:
        cfg = self._config()
        cfg.audio.music_start_mode = "after_hook_effect"

        with (
            patch("src.assembly._probe_duration", return_value=20.0),
            patch("src.assembly._run") as run_cmd,
        ):
            add_background_music(
                Path("final_clean.mp4"),
                Path("music.mp3"),
                Path("final_music.mp4"),
                cfg,
                start_sec=4.153,
            )

        cmd = run_cmd.call_args.args[0]
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("adelay=4153|4153", filter_complex)
        self.assertIn("afade=t=in:st=4.153:d=1.000", filter_complex)

    def test_broll_alternates_zoom_out_then_zoom_in(self) -> None:
        self.assertEqual(_alternating_broll_motion_preset(0), "zoom_out_soft")
        self.assertEqual(_alternating_broll_motion_preset(1), "zoom_in_soft")
        self.assertEqual(_alternating_broll_motion_preset(2), "zoom_out_soft")

    def test_broll_flash_applies_every_other_cut(self) -> None:
        cfg = self._config()

        self.assertFalse(_should_flash_broll_cut(0, cfg))
        self.assertTrue(_should_flash_broll_cut(1, cfg))
        self.assertFalse(_should_flash_broll_cut(2, cfg))
        self.assertTrue(_should_flash_broll_cut(3, cfg))

    def test_xfade_transitions_disable_flash_to_avoid_double_treatment(self) -> None:
        cfg = self._config()
        cfg.transition.enabled = True
        cfg.transition.kind = "dissolve"

        self.assertTrue(_broll_transitions_enabled(cfg))
        # Flash and xfade are competing styles — flash must yield.
        self.assertFalse(_should_flash_broll_cut(1, cfg))
        self.assertFalse(_should_flash_broll_cut(3, cfg))

    def test_broll_transitions_disabled_for_cut_kind(self) -> None:
        cfg = self._config()
        cfg.transition.enabled = True
        cfg.transition.kind = "cut"
        self.assertFalse(_broll_transitions_enabled(cfg))

    def test_xfade_kind_mapping_covers_all_transition_kinds(self) -> None:
        # Every non-cut TransitionKind must map to a real ffmpeg xfade name.
        self.assertEqual(_XFADE_NAME_BY_KIND["dissolve"], "fade")
        self.assertEqual(_XFADE_NAME_BY_KIND["fade"], "fade")
        self.assertEqual(_XFADE_NAME_BY_KIND["dipblack"], "fadeblack")
        self.assertEqual(_XFADE_NAME_BY_KIND["dipwhite"], "fadewhite")
        self.assertEqual(_XFADE_NAME_BY_KIND["wipeleft"], "wipeleft")
        self.assertEqual(_XFADE_NAME_BY_KIND["slideup"], "slideup")

    def test_center_zoompan_chain_is_center_locked(self) -> None:
        chain = _center_zoompan_chain(
            zoom_in=False,
            width=1080,
            height=1920,
            duration_sec=1.8,
            delta=0.05,
            fps=30,
        )

        self.assertIn("fps=30", chain)
        self.assertIn("scale=2160:3840:flags=lanczos", chain)
        self.assertIn("zoompan=z='1+(0.05000*(1-(", chain)
        self.assertIn("x='iw/2-(iw/zoom/2)'", chain)
        self.assertIn("y='ih/2-(ih/zoom/2)'", chain)
        self.assertIn("d=1:fps=30:s=2160x3840", chain)

    def test_cta_punch_prefers_central_action_word(self) -> None:
        transcript = Transcript(
            words=[
                Word(word="Попробовать", start=0.44, end=0.72),
                Word(word="можно", start=0.72, end=0.84),
                Word(word="бесплатно,", start=0.84, end=1.42),
                Word(word="ссылка", start=1.42, end=1.86),
                Word(word="в", start=1.86, end=1.92),
                Word(word="описании.", start=1.92, end=2.12),
            ],
            full_text="Попробовать можно бесплатно ссылка в описании.",
            duration=2.12,
        )

        cue = _select_cta_punch_cue(transcript, media_duration_sec=3.06)

        self.assertIsNotNone(cue)
        assert cue is not None
        self.assertEqual(cue.word.word, "ссылка")

    def test_cta_punch_avoids_tail_prompt_when_link_word_exists(self) -> None:
        transcript = Transcript(
            words=[
                Word(word="попробовать", start=1.06, end=1.42),
                Word(word="можно", start=1.42, end=1.56),
                Word(word="бесплатно", start=1.56, end=2.20),
                Word(word="ссылку", start=2.20, end=2.76),
                Word(word="я", start=2.76, end=2.84),
                Word(word="оставил", start=2.84, end=3.16),
                Word(word="в", start=3.16, end=3.22),
                Word(word="шапке", start=3.22, end=3.48),
                Word(word="профиля", start=3.48, end=3.86),
                Word(word="давай", start=3.86, end=4.14),
            ],
            full_text="попробовать можно бесплатно ссылку я оставил в шапке профиля давай",
            duration=4.14,
        )

        cue = _select_cta_punch_cue(transcript, media_duration_sec=5.47)

        self.assertIsNotNone(cue)
        assert cue is not None
        self.assertEqual(cue.word.word, "ссылку")

    def test_cta_lead_trim_uses_first_word_offset(self) -> None:
        transcript = Transcript(
            words=[
                Word(word="попробовать", start=1.06, end=1.42),
                Word(word="можно", start=1.42, end=1.56),
            ],
            full_text="попробовать можно",
            duration=1.56,
        )

        trim = _cta_lead_trim_sec(transcript, media_duration_sec=5.47)

        self.assertAlmostEqual(trim, 0.98, places=2)

    def test_cta_duration_limit_keeps_last_spoken_word(self) -> None:
        cfg = self._config()
        transcript = Transcript(
            words=[
                Word(word="Когда", start=0.50, end=0.66),
                Word(word="будешь", start=0.66, end=0.88),
                Word(word="обязательно", start=3.82, end=4.14),
                Word(word="проверь", start=4.14, end=4.88),
                Word(word="достижений", start=6.64, end=7.14),
                Word(word="работу", start=8.06, end=8.38),
            ],
            full_text="Когда будешь обязательно проверь достижений работу",
            duration=8.14,
        )

        limit = _cta_duration_limit_sec(
            transcript,
            lead_trim_sec=0.42,
            media_duration_sec=9.45,
            cfg=cfg,
        )

        self.assertGreater(limit, cfg.render_guardrails.cta_target_max_sec)
        self.assertGreaterEqual(limit, transcript.words[-1].end - 0.42)

    def test_cta_duration_limit_keeps_spoken_tail_past_target(self) -> None:
        cfg = self._config()
        transcript = Transcript(
            words=[
                Word(word="сохрани", start=1.00, end=1.30),
                Word(word="формулу", start=1.30, end=1.80),
                Word(word="используй", start=6.05, end=6.40),
                Word(word="отклике", start=7.10, end=7.42),
                Word(word="давай", start=7.70, end=8.10),
            ],
            full_text="сохрани формулу используй отклике давай",
            duration=8.10,
        )

        lead_trim = _cta_lead_trim_sec(transcript, media_duration_sec=9.25)
        limit = _cta_duration_limit_sec(
            transcript,
            lead_trim_sec=lead_trim,
            media_duration_sec=9.25,
            cfg=cfg,
        )

        self.assertGreater(limit, cfg.render_guardrails.cta_target_max_sec)
        self.assertGreaterEqual(limit, transcript.words[-1].end - lead_trim)

    def test_guardrail_allows_cta_over_target_when_duration_preserves_speech(self) -> None:
        cfg = self._config()

        _enforce_render_guardrails(
            cfg,
            hook_montage=HookMontageDiagnostics(enabled=False),
            cta_duration_sec=cfg.render_guardrails.cta_target_max_sec + 1.2,
        )

    def test_cta_punch_zoompan_combines_zoom_out_and_punch_in(self) -> None:
        chain = _cta_punch_zoompan_chain(
            width=1080,
            height=1920,
            duration_sec=3.06,
            punch_start_sec=1.84,
            punch_duration_sec=0.36,
            fps=30,
        )

        self.assertIn("zoompan=z='1+(0.07000*(1-(", chain)
        self.assertIn("+(0.11500*(", chain)
        self.assertIn("(it-1.840)/0.360", chain)
        self.assertIn("d=1:fps=30:s=2160x3840", chain)

    def test_normalize_cta_clip_ducks_voice_and_boosts_swoosh(self) -> None:
        cfg = self._config()
        transcript = Transcript(
            words=[
                Word(word="Попробовать", start=0.44, end=0.72),
                Word(word="можно", start=0.72, end=0.84),
                Word(word="бесплатно,", start=0.84, end=1.42),
                Word(word="ссылка", start=1.42, end=1.86),
                Word(word="в", start=1.86, end=1.92),
                Word(word="описании.", start=1.92, end=2.12),
            ],
            full_text="Попробовать можно бесплатно ссылка в описании.",
            duration=2.12,
        )

        with (
            patch("src.assembly._probe_duration", side_effect=[3.06, 0.653063]),
            patch("src.assembly._video_normalize_chain", return_value="scale=1080:1920"),
            patch("src.assembly._copy_ass_to_temp"),
            patch("src.assembly._run") as run_cmd,
        ):
            _normalize_cta_clip(
                Path("assets/ctas/cta_4.mp4"),
                Path("cta_out.mp4"),
                cfg,
                transcript=transcript,
                burn_ass=None,
                look_profile=None,
            )

        cmd = run_cmd.call_args.args[0]
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("[0:a]atrim=start=0.360,asetpts=PTS-STARTPTS", filter_complex)
        self.assertIn("volume=0.78:enable='between(t,0.920,1.400)'", filter_complex)
        self.assertIn("afade=t=out:st=0.553:d=0.10", filter_complex)
        self.assertIn("volume=-3.00dB,adelay=960|960", filter_complex)

    def test_normalize_cta_clip_preserves_speech_past_target(self) -> None:
        cfg = self._config()
        transcript = Transcript(
            words=[
                Word(word="обязательно", start=0.60, end=1.00),
                Word(word="проверь", start=1.00, end=1.40),
                Word(word="лишнее", start=6.10, end=6.40),
            ],
            full_text="обязательно проверь лишнее",
            duration=8.00,
        )

        with (
            patch("src.assembly._probe_duration", side_effect=[9.0, 0.653063]),
            patch("src.assembly._video_normalize_chain", return_value="scale=1080:1920"),
            patch("src.assembly._run") as run_cmd,
        ):
            _normalize_cta_clip(
                Path("assets/ctas/cta_22.mov"),
                Path("cta_out.mp4"),
                cfg,
                transcript=transcript,
            )

        cmd = run_cmd.call_args.args[0]
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("trim=duration=6.230", filter_complex)
        self.assertIn("atrim=duration=6.230", filter_complex)

    def test_hook_flash_transition_overlays_end_of_hook_section(self) -> None:
        cfg = self._config()

        with (
            patch("src.assembly._probe_duration", return_value=8.8),
            patch("src.assembly._run") as run_cmd,
        ):
            _apply_hook_flash_to_hook_tail(
                Path("hook_norm.mp4"),
                Path("hook_norm_flash.mp4"),
                cfg,
            )

        cmd = run_cmd.call_args.args[0]
        self.assertIn("color=c=0xffb14a:s=1080x1920:r=30:d=8.800", cmd)
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("colorchannelmixer=aa=0.880", filter_complex)
        self.assertIn("fade=t=in:st=8.480:d=0.060:alpha=1", filter_complex)
        self.assertIn("fade=t=out:st=8.540:d=0.260:alpha=1", filter_complex)
        self.assertIn("overlay=x=0:y=0:eof_action=pass", filter_complex)
        self.assertIn("fps=30,setpts=PTS-STARTPTS,setsar=1", filter_complex)

    def test_concat_copy_rejects_timeline_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            parts = [tmp / "a.mp4", tmp / "b.mp4"]
            concat_list = tmp / "concat.txt"
            out = tmp / "out.mp4"

            with (
                patch("src.assembly.subprocess.run") as run_cmd,
                patch("src.assembly._probe_duration", side_effect=[5.0, 5.0, 16.0]),
            ):
                run_cmd.return_value.returncode = 0
                ok = _concat_copy(parts, concat_list, out, "test concat")

        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
