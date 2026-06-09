# Flexi

**Агентный пайплайн для создания вертикальных коротких видео** (9:16,
1080×1920, 30fps) — субтитры, закадровая озвучка через TTS, сборка b-roll, чистка
talking-head, монтаж в стиле ref-style, комбинаторные рилсы и формат 3-strip. Ты управляешь
им из командной строки (и он рассчитан на управление кодинг-агентом вроде
Claude Code, который сам онбордится из `CLAUDE.md`).

> Flexi — это переиспользуемый движок. Он поставляется **без медиа** — приноси свои
> материалы, музыку и SFX. Шрифты идут в комплекте под открытыми лицензиями.

**🇷🇺 Не разработчик / по-русски?** Открой **[НАЧНИ_ЗДЕСЬ.md](НАЧНИ_ЗДЕСЬ.md)** —
пошаговый онбординг простыми словами. Можно вообще ничего не настраивать руками:
попроси агента «установи всё, что нужно» — он сам поставит и скажет, когда
готово. Проверка готовности: `make check` (или `python3 check_setup.py`).

## Пайплайны

| Пайплайн | Что делает |
|---|---|
| **Standard / library** | Готовый хук + закадровая озвучка через TTS + авто/ручной b-roll + CTA + субтитры + музыка, из небольшого `VideoScript` JSON. |
| **Talking-head clean** | Чистит сырой материал talking-head: удаление тишины/дублей, HDR→SDR, вертикальный формат, опциональные субтитры. |
| **Ref-style directed** | Семантический режиссёр раскладывает транскрипт по 5 визуальным форматам с продуктовым b-roll, вшитыми подписями и музыкой. |
| **Reel Matrix** | Смешивает взаимозаменяемые блоки хук × порядок-советов × cta во множество уникальных видео. |
| **3-strip** | Три горизонтальных клипа, сложенных в один кадр 9:16, асинхронным каскадом. |

Плюс **слой QA**, который проверяет готовые видео (соответствие спецификации, мёртвый эфир, размытие HDR,
подписи, громкость). Смотри `docs/modes.md`, как стадии связываются между собой.

## Требования

- **ffmpeg** (с libass) и **ffprobe**
- **Python** ≥ 3.10
- **Ключ API ElevenLabs** — только для закадровой озвучки через TTS ([elevenlabs.io](https://elevenlabs.io))
- **zsh** — только для пайплайна 3-strip

## Установка

```bash
git clone <your-fork-url> flexi && cd flexi
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # затем впиши свой ELEVENLABS_API_KEY
.venv/bin/python -m pytest    # интеграционные тесты, которым нужны медиа, авто-пропускаются
python3 check_setup.py        # проверка готовности (по-русски): зависимости, медиа, что можно собрать
```

## Использование

```bash
# Standard / library — собрать рилс из VideoScript JSON
.venv/bin/python -m src.cli build scripts/example.json
.venv/bin/python -m src.cli validate scripts/example.json
.venv/bin/python -m src.cli --help          # все подкоманды

# Talking-head clean / Ref-style / Reel Matrix
.venv/bin/python pipelines/render_talking_head_dynamic_clean.py --help
.venv/bin/python pipelines/render_ref_style_directed.py --help
.venv/bin/python pipelines/reel_matrix.py --help

# 3-strip (управляется конфигом)
cp pipelines/three_strip/episodes/example.conf pipelines/three_strip/episodes/myday.conf
zsh pipelines/three_strip/build_3strip.zsh pipelines/three_strip/episodes/myday.conf
```

Или через Makefile: `make test`, `make validate SCRIPT=…`, `make build SCRIPT=…`.

> `scripts/example.json` показывает формат `VideoScript`. `validate` и `build` разрешают `hook_id` / `cta_id` / b-roll из `assets/*/_meta.json`, поэтому сначала зарегистрируй там свои клипы хука / CTA / b-roll (см. `assets/README.md`).

## Конфигурация

`config.yaml` — единственный источник истины для разрешения, кодеков, уровней
звука, модели/скорости TTS, стилей субтитров, ритма, ротации b-roll и
монтажа хука. Меняй поведение там, а не хардкодом.

Добавляй свои медиа в `assets/` (см. `assets/README.md` про раскладку). Шрифты в
`assets/fonts/` идут в комплекте (под открытыми лицензиями); дефолты рендерят субтитры из
коробки. Чтобы использовать коммерческие шрифты, которыми ты владеешь (например, Gilroy, Druk Wide), положи их
сюда и укажи в `config.yaml` имя семейства — см. `assets/fonts/README.md`.

## Управление через агента

Этот репозиторий устроен так, чтобы кодинг-агент сам онбордился: `CLAUDE.md` читается при
запуске и индексирует каждый пайплайн, шаг настройки и правило работы; `AGENTS.md`
содержит рабочий контракт; а `.claude/skills/video-montage/` — это
пошаговый навык по ffmpeg/TTS/субтитрам. Клонируй, открой в своём агенте и попроси его
собрать рилс.

## Структура проекта

```
src/         общая библиотека движка + `src.cli`
pipelines/   точки входа всех пайплайнов (вкл. three_strip/)
docs/        modes.md + руководства по управлению через агента
tests/       набор тестов pytest (интеграционные авто-пропускаются без медиа)
assets/      встроенные шрифты + твои медиа (приносишь сам)
raw/         твой инбокс — агент раскладывает его в assets/ + scripts/
scripts/     входные VideoScript JSON (example.json)
config.yaml  все настройки рендера/TTS/субтитров/звука
check_setup.py · НАЧНИ_ЗДЕСЬ.md  проверка готовности + онбординг для человека (по-русски)
```

## Лицензия

MIT — см. [LICENSE](LICENSE). Шрифты в комплекте — под лицензией SIL Open Font License
(см. `assets/fonts/`).
