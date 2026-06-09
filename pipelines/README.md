# Пайплайны (pipelines/)

Все пайплайны генерации видео живут здесь, в одном месте. Они используют общую
библиотеку движка в `src/` (схемы, транскрипция, субтитры, сборка). Запускайте
каждый из корня репозитория, чтобы `src.*` и `pipelines.*` корректно
разрешались.

| Пайплайн | Файл | Кратко |
|---|---|---|
| **Standard / library** | `src/cli.py` (`python -m src.cli build`) | Хук + тело на TTS + авто/ручной b-roll + CTA + субтитры + музыка из `VideoScript` JSON. |
| **Talking-head clean** | `render_talking_head_dynamic_clean.py` (+ `talking_head_retake_planner.py`) | Чистка сырого talking-head материала: удаление пауз/дублей, HDR→SDR, вертикаль, опционально субтитры. |
| **Ref-style directed** | `render_ref_style_directed.py` | Смысловой режиссёр раскладывает транскрипт по 5 визуальным форматам с продуктовым b-roll, подписями, музыкой. |
| **Reel Matrix** | `reel_matrix.py` | Смешивает взаимозаменяемые блоки хук × порядок-подсказок × cta во множество уникальных видео. |
| **3-strip** | `three_strip/build_3strip.zsh` | Три горизонтальных клипа, сложенных в 9:16, асинхронный каскад. Управляется конфигом. |
| **QA (ref-style)** | `qa_ref_style.py` | Проверяет готовое видео: спецификацию, мёртвый эфир, размытие HDR, подписи, громкость. |

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
```
