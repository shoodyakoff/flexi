# Voiceover Style

These rules define how agents should prepare `voiceover_text` for generated reels.
They are intentionally repository-level rules so Codex, Claude, and future agents
make the same choices.

## Markers

- Use `{{pause:0.30}}` for an explicit pause in seconds.
- Existing `<<0.30>>` markers are supported for old scripts, but new scripts should use `{{pause:0.30}}`.
- Use `{{slow}}...{{/slow}}` for a short phrase that should be spoken slightly slower.
- Do not put markers into subtitles manually. The pipeline strips markers from alignment and display text.
- Put pronunciation fixes into `assets/pronunciation.yaml` instead of repeating stress marks in every script.

## Pauses

- Add `{{pause:0.25}}` to `{{pause:0.35}}` between short list items that would otherwise sound rushed.
- Add `{{pause:0.30}}` to `{{pause:0.40}}` after structural beats like `Первое.`, `Второе.`, `И нет.`, `А именно.`
- Add `{{pause:0.30}}` to `{{pause:0.40}}` when the thought changes sharply.
- Add a small pause before a brand, platform, or product name when it is the point of the phrase.
- Do not add pauses after every sentence. The result should stay conversational, not chopped.

## Slowdown

- The pipeline automatically slows the final voiceover segment a little.
- Use `{{slow}}...{{/slow}}` manually only for punchlines, important numbers, or the emphasized side of `не X, а Y`.
- Prefer slowing the last 2-4 words of a sentence, not a whole paragraph.
- For numeric proof points, slow the result, not the setup: `с 4200 до {{slow}}1800 рублей{{/slow}}`.

## Pronunciation

- Add recurring stress fixes to `assets/pronunciation.yaml`.
- Use combining acute stress only for one-off words when the dictionary would be too broad.
- Keep subtitles clean: no stress marks, no caps-only pronunciation hacks, no agent notes.
- Known fixes start with lead forms: `ли́д`, `ли́да`, `ли́ды`, `ли́дов`.

## Example

Raw idea:

```text
Не занимался рекламой, а Яндекс Директ. Метрика. Работа с воронкой.
Не развивал канал, а снизил стоимость лида с 4200 до 1800 рублей за полгода.
```

Voiceover text:

```text
Не занимался рекламой, а {{slow}}Яндекс Директ{{/slow}}. {{pause:0.25}} Метрика. {{pause:0.25}} Работа с воронкой.
Не развивал канал, а снизил стоимость лида с 4200 до {{slow}}1800 рублей за полгода.{{/slow}}
```
