# Пайплайны (pipelines/)

Все пайплайны генерации видео живут здесь, в одном месте. Они используют общую
библиотеку движка в `src/` (схемы, транскрипция, субтитры, сборка). Запускайте
каждый из корня репозитория, чтобы `src.*` и `pipelines.*` корректно
разрешались.

| Режим (рус) | Файл | Кратко |
|---|---|---|
| **Broll-рилс с ElevenLabs** *(Standard / library)* | `src/cli.py` (`python -m src.cli build`) | AI-озвучка (ElevenLabs) + авто/ручной b-roll + субтитры + музыка, плюс хук и CTA из библиотеки. Из `VideoScript` JSON. |
| **Говорящая голова** *(talking-head clean)* | `render_talking_head_dynamic_clean.py` (+ `talking_head_retake_planner.py`) | Чистка сырого talking-head материала: удаление пауз/дублей, HDR→SDR, вертикаль, опционально субтитры. |
| **Демо продукта** *(ref-style — в разработке)* | `render_ref_style_directed.py` | Смысловой режиссёр раскладывает транскрипт по 5 визуальным форматам с продуктовым b-roll, подписями, музыкой. |
| **Много рилсов** *(Reel Matrix — в разработке)* | `reel_matrix.py` | Из сменных блоков (вступления × серединки × концовки) собирает все сочетания — множество уникальных рилсов. |
| **Динамичный рилс** *(3-strip)* | `three_strip/build_3strip.zsh` | Три горизонтальных клипа, сложенных в 9:16, асинхронный каскад. Управляется конфигом. |
| **shnurok** *(рекламный рилс кроссовок)* | `src/cli.py` (`python -m src.cli shnurok`) | Хук (говорящая голова + титры-лесенка + влёт графики) → тело (b-roll под готовую озвучку + пословные сабы) → CTA. Стили classic/bold. Плейбук: `shnurok/README.md`. |
| **QA (ref-style)** | `qa_ref_style.py` | Проверяет готовое видео: спецификацию, мёртвый эфир, размытие HDR, подписи, громкость. |
| **QA (shnurok)** | `qa_shnurok.py` | Проверяет готовое видео: пословные сабы без наложения, формат, аудио-дедуп. |

Каждая точка входа на Python поддерживает `--help`. Смотрите `../docs/modes.md`
о том, как стадии связываются друг с другом, и `../CLAUDE.md` о том, когда какой
маршрут использовать.

## Быстрый старт по каждому пайплайну

```bash
# Standard / library
python -m src.cli build scripts/example.json

# Talking-head clean
python pipelines/render_talking_head_dynamic_clean.py --help

# Ref-style directed
python pipelines/render_ref_style_directed.py --help

# Reel Matrix (сначала закиньте клипы в raw/)
python pipelines/reel_matrix.py ingest --raw raw/
python pipelines/reel_matrix.py dryrun --dir output/matrix

# 3-strip (скопируйте episodes/example.conf -> episodes/<day>.conf, затем)
zsh pipelines/three_strip/build_3strip.zsh pipelines/three_strip/episodes/example.conf

# QA готового видео
python pipelines/qa_ref_style.py --final output/<slug>/final.mp4

# shnurok (закиньте исходники одного ролика в одну папку, см. shnurok/README.md)
python -m src.cli shnurok assets/shnurok_test --style both
python pipelines/qa_shnurok.py --slug shnurok_test
```
