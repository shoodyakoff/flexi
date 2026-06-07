from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from src.cli import _pronunciation_rules_from_yaml
from src.schemas import TTSConfig, TTSPronunciationDictionaryLocator
from src.tts import _render_text_segment, _text_hash, _tts_request_kwargs, _write_tts_stream
from src.tts_text import (
    compile_tts_markup,
    prepare_subtitle_text,
    prepare_tts_text,
    prepare_voiceover_text,
)


class TTSPronunciationContractTest(unittest.TestCase):
    def test_pronunciation_yaml_builds_official_alias_rules(self) -> None:
        from pathlib import Path

        rules = _pronunciation_rules_from_yaml(
            Path(__file__).resolve().parents[1] / "assets" / "pronunciation.yaml"
        )
        aliases = {rule["string_to_replace"]: rule["alias"] for rule in rules}

        self.assertEqual(aliases["лида"], "лида́")
        self.assertEqual(aliases["1800 рублей"], "тысячи восьмисот рублей")
        self.assertTrue(all(rule["type"] == "alias" for rule in rules))
        self.assertTrue(all(rule["word_boundaries"] for rule in rules))

    def test_common_terms_are_not_rewritten_locally_for_tts(self) -> None:
        text = "ATS HR Сопровод айтиэс эйтиэс"

        self.assertEqual(
            prepare_tts_text(text),
            "ATS HR Сопровод айтиэс эйтиэс",
        )
        self.assertEqual(
            prepare_voiceover_text(text).tts_text,
            "ATS HR Сопровод айтиэс эйтиэс",
        )
        self.assertEqual(
            prepare_voiceover_text(text).align_text,
            "ATS HR Сопровод айтиэс эйтиэс",
        )
        self.assertEqual(prepare_voiceover_text(text).display_text, "ATS HR Сопровод ATS ATS")

    def test_stress_marks_are_tts_only(self) -> None:
        text = "Сопрово\u0301д работает {{ product_break }} дальше"

        plan = prepare_voiceover_text(text)

        self.assertEqual(plan.tts_text, "Сопрово\u0301д работает {{ product_break }} дальше")
        self.assertEqual(plan.align_text, "Сопровод работает дальше")
        self.assertEqual(plan.display_text, "Сопровод работает дальше")
        self.assertEqual(plan.product_break_after_token, 2)
        self.assertEqual(prepare_subtitle_text("Сопрово\u0301д"), "Сопровод")

    def test_pause_markers_compile_inline_for_v2(self) -> None:
        text = "Если [[зацепишь]]{{pause:0.15}}рекрутер. Не зацепило<<0.45>>до свидания."

        plan = prepare_voiceover_text(text)

        self.assertEqual(
            plan.tts_text,
            'Если зацепишь <break time="0.15s" /> рекрутер. '
            'Не зацепило <break time="0.45s" /> до свидания.',
        )
        self.assertEqual(
            plan.align_text,
            "Если зацепишь рекрутер. Не зацепило до свидания.",
        )
        self.assertEqual(
            plan.display_text,
            "Если зацепишь рекрутер. Не зацепило до свидания.",
        )

    def test_v3_pause_markers_compile_to_audio_tags(self) -> None:
        text = "Если [[зацепишь]]{{pause:0.15}}рекрутер."

        self.assertEqual(
            compile_tts_markup(text, markup_dialect="v3"),
            "Если зацепишь [pause] рекрутер.",
        )

    def test_v3_keeps_only_allowed_audio_tags_out_of_alignment(self) -> None:
        text = "[calm] Если {{pause:0.15}} зацепишь [excited] рекрутер."

        plan = prepare_voiceover_text(text, markup_dialect="v3")

        self.assertEqual(plan.tts_text, "[calm] Если [pause] зацепишь рекрутер.")
        self.assertEqual(plan.align_text, "Если зацепишь рекрутер.")
        self.assertEqual(plan.display_text, "Если зацепишь рекрутер.")

    def test_v2_strips_v3_audio_tags(self) -> None:
        text = "[calm] Если {{pause:0.15}} зацепишь."

        plan = prepare_voiceover_text(text, markup_dialect="v2")

        self.assertEqual(plan.tts_text, 'Если <break time="0.15s" /> зацепишь.')
        self.assertEqual(plan.align_text, "Если зацепишь.")

    def test_em_dash_stays_as_punctuation(self) -> None:
        text = "Самое первое — должность и профиль."

        plan = prepare_voiceover_text(text)

        self.assertEqual(
            plan.tts_text,
            "Самое первое — должность и профиль.",
        )
        self.assertEqual(plan.align_text, "Самое первое — должность и профиль.")

    def test_slow_markers_are_removed_without_segmenting(self) -> None:
        text = "Снизил стоимость {{slow}}лида за полгода{{/slow}}."

        plan = prepare_voiceover_text(text)

        self.assertEqual(plan.tts_text, "Снизил стоимость лида за полгода.")
        self.assertEqual(plan.display_text, "Снизил стоимость лида за полгода.")

    def test_tts_kwargs_include_official_api_pronunciation_controls(self) -> None:
        settings = TTSConfig(
            model_id="eleven_turbo_v2_5",
            output_format="mp3_44100_128",
            language_code="ru",
            apply_text_normalization="on",
            pronunciation_dictionary_locators=[
                TTSPronunciationDictionaryLocator(
                    pronunciation_dictionary_id="dict_123",
                    version_id="ver_456",
                )
            ],
        )

        kwargs = _tts_request_kwargs(
            voice_id="voice_123",
            text="Сопрово\u0301д",
            settings=settings,
        )

        self.assertEqual(kwargs["voice_id"], "voice_123")
        self.assertEqual(kwargs["text"], "Сопрово\u0301д")
        self.assertEqual(kwargs["model_id"], "eleven_turbo_v2_5")
        self.assertEqual(kwargs["output_format"], "mp3_44100_128")
        self.assertEqual(kwargs["language_code"], "ru")
        self.assertEqual(kwargs["apply_text_normalization"], "on")
        self.assertNotIn("seed", kwargs)
        self.assertNotIn("voice_settings", kwargs)
        self.assertNotIn("previous_text", kwargs)
        self.assertNotIn("next_text", kwargs)
        self.assertEqual(len(kwargs["pronunciation_dictionary_locators"]), 1)

        locator = kwargs["pronunciation_dictionary_locators"][0]
        self.assertEqual(locator.pronunciation_dictionary_id, "dict_123")
        self.assertEqual(locator.version_id, "ver_456")

    def test_direct_tts_hash_tracks_only_sent_api_controls(self) -> None:
        base = TTSConfig()
        with_model = base.model_copy(update={"model_id": "eleven_flash_v2_5"})
        with_dictionary = base.model_copy(
            update={
                "pronunciation_dictionary_locators": [
                    TTSPronunciationDictionaryLocator(
                        pronunciation_dictionary_id="dict_123",
                        version_id="ver_456",
                    )
                ]
            }
        )

        baseline_hash = _text_hash("Сопровод", base, 0.5, 0.8)

        self.assertNotEqual(
            baseline_hash,
            _text_hash("Сопровод", with_model, 0.5, 0.8),
        )
        self.assertNotEqual(
            baseline_hash,
            _text_hash("Сопровод", with_dictionary, 0.5, 0.8),
        )
        self.assertNotEqual(
            baseline_hash,
            _text_hash("Сопрово\u0301д", base, 0.5, 0.8),
        )

    def test_pronunciation_dictionary_limit_matches_elevenlabs_api(self) -> None:
        with self.assertRaises(ValidationError):
            TTSConfig(
                pronunciation_dictionary_locators=[
                    TTSPronunciationDictionaryLocator(
                        pronunciation_dictionary_id=f"dict_{idx}",
                        version_id=f"ver_{idx}",
                    )
                    for idx in range(4)
                ]
            )

    def test_old_chunked_and_segmented_paths_are_removed(self) -> None:
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "src" / "tts.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("chunked", source)
        self.assertNotIn("_trim_chunk_edges", source)
        self.assertNotIn("_synthesize_segmented", source)
        self.assertNotIn("_synthesize_voiceover_segments", source)

    def test_write_tts_stream_does_not_replace_existing_audio_on_stream_failure(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "voiceover.mp3"
            out_path.write_bytes(b"previous audio")

            def broken_stream():
                yield b"partial"
                raise RuntimeError("ssl eof")

            with self.assertRaisesRegex(RuntimeError, "ssl eof"):
                _write_tts_stream(broken_stream(), out_path)

            self.assertEqual(out_path.read_bytes(), b"previous audio")
            self.assertFalse((Path(tmp_dir) / "voiceover.mp3.tmp").exists())

    def test_render_text_segment_retries_failed_tts_stream(self) -> None:
        class FakeTextToSpeech:
            def __init__(self) -> None:
                self.calls = 0

            def convert(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    def broken_stream():
                        yield b"partial"
                        raise RuntimeError("ssl eof")

                    return broken_stream()
                return [b"final audio"]

        class FakeClient:
            def __init__(self) -> None:
                self.text_to_speech = FakeTextToSpeech()

        with TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "voiceover.mp3"
            client = FakeClient()

            _render_text_segment(
                client,  # type: ignore[arg-type]
                "voice_123",
                "Текст озвучки",
                out_path,
                TTSConfig(),
            )

            self.assertEqual(client.text_to_speech.calls, 2)
            self.assertEqual(out_path.read_bytes(), b"final audio")


if __name__ == "__main__":
    unittest.main()
