#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Flexi — проверка готовности (setup readiness check).

Запусти ДО первой сборки:

    python3 check_setup.py        (или:  make check)

Скрипт работает на чистом клоне — нужен только системный python3, без установки
зависимостей. Он по-русски и простыми словами говорит: что уже готово, чего не
хватает (и какой командой это починить) и что ты прямо сейчас можешь собрать.

Это же делает агент: он запускает проверку и объясняет тебе результат.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

try:  # на macOS/Linux уже utf-8; на Windows подстрахуемся
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent

VIDEO_EXT = {".mp4", ".mov", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac"}

# ── вывод ────────────────────────────────────────────────────────────────
_tty = sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _tty else s


def green(s): return _c("32", s)
def red(s): return _c("31", s)
def yellow(s): return _c("33", s)
def dim(s): return _c("2", s)
def bold(s): return _c("1", s)
def cyan(s): return _c("36", s)


def header(title: str) -> None:
    print()
    print(bold(cyan(title)))


def item(status: str, text: str, hint: str | None = None) -> None:
    sym = {"ok": green("✓"), "bad": red("✗"), "warn": yellow("⚠")}[status]
    print(f"   {sym} {text}")
    if hint:
        print(f"     {dim('→ ' + hint)}")


# ── мелкие проверки ──────────────────────────────────────────────────────
def which(prog: str) -> str | None:
    from shutil import which as _which
    return _which(prog)


def ffmpeg_has_libass(ffmpeg: str) -> bool:
    """Фильтр `subtitles` есть только когда ffmpeg собран с libass."""
    try:
        out = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=20,
        ).stdout
        if re.search(r"\bsubtitles\b", out):
            return True
        ver = subprocess.run(
            [ffmpeg, "-hide_banner", "-version"],
            capture_output=True, text=True, timeout=20,
        ).stdout
        return "enable-libass" in ver
    except Exception:
        return False


def venv_python() -> Path | None:
    for c in (
        ROOT / ".venv" / "bin" / "python",
        ROOT / ".venv" / "bin" / "python3",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ):
        if c.exists():
            return c
    return None


def venv_deps_ok(py: Path) -> bool:
    try:
        r = subprocess.run(
            [str(py), "-c", "import typer, pydantic, yaml, rich, dotenv"],
            capture_output=True, text=True, timeout=30,
        )
        return r.returncode == 0
    except Exception:
        return False


def env_key_filled(name: str) -> bool:
    envf = ROOT / ".env"
    if not envf.exists():
        return False
    for raw in envf.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if line.startswith(name + "="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            return bool(val)
    return False


def count_media(folder: Path, exts: set[str]) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for f in folder.iterdir() if f.is_file() and f.suffix.lower() in exts)


def count_raw() -> int:
    """Файлы, лежащие во входящих (raw/), кроме служебных."""
    folder = ROOT / "raw"
    if not folder.is_dir():
        return 0
    skip = {"readme.md", ".gitkeep", ".ds_store"}
    return sum(1 for f in folder.iterdir() if f.is_file() and f.name.lower() not in skip)


def count_raw_video() -> int:
    folder = ROOT / "raw"
    if not folder.is_dir():
        return 0
    return sum(1 for f in folder.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_EXT)


# ── основная проверка ────────────────────────────────────────────────────
def main() -> int:
    print()
    print(bold("=" * 64))
    print(bold("  Flexi — проверка готовности"))
    print(bold("=" * 64))
    print(dim("  Flexi делает вертикальные видео 9:16 (рилсы) из твоих кусков"))
    print(dim("  видео, музыки и звуков. Ниже — всё ли готово для сборки."))

    blockers: list[str] = []  # критичное: без этого ничего не соберётся

    # 1) Системные программы
    header("1) Программы на компьютере")
    ffmpeg = which("ffmpeg")
    if ffmpeg:
        item("ok", f"ffmpeg найден  {dim('(' + ffmpeg + ')')}")
        if ffmpeg_has_libass(ffmpeg):
            item("ok", "ffmpeg умеет субтитры (libass)")
        else:
            item("warn", "ffmpeg без поддержки субтитров (libass)",
                 "субтитры могут не отрисоваться; на macOS: brew reinstall ffmpeg")
    else:
        item("bad", "ffmpeg не найден",
             "macOS: brew install ffmpeg · Ubuntu: sudo apt install ffmpeg")
        blockers.append("ffmpeg")

    if which("ffprobe"):
        item("ok", "ffprobe найден")
    else:
        item("bad", "ffprobe не найден (ставится вместе с ffmpeg)",
             "macOS: brew install ffmpeg")
        blockers.append("ffprobe")

    pv = sys.version_info
    if pv >= (3, 10):
        item("ok", f"Python {pv.major}.{pv.minor}  {dim('(нужно ≥ 3.10, рекомендуется 3.12)')}")
    else:
        item("bad", f"Python {pv.major}.{pv.minor} — слишком старый",
             "нужен Python ≥ 3.10 (python.org)")
        blockers.append("python")

    # 2) Окружение Python
    header("2) Окружение Python (.venv)")
    py = venv_python()
    if not py:
        item("bad", "Виртуальное окружение .venv не создано",
             "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")
        blockers.append("venv")
    elif not venv_deps_ok(py):
        item("warn", "Окружение есть, но библиотеки не установлены (или не все)",
             ".venv/bin/pip install -r requirements.txt")
        blockers.append("deps")
    else:
        item("ok", "Окружение .venv готово, библиотеки на месте")

    # 3) Ключи (.env) — нужны ТОЛЬКО для озвучки
    header("3) Ключ для озвучки (.env)")
    tts = env_key_filled("ELEVENLABS_API_KEY")
    if tts:
        item("ok", "ELEVENLABS_API_KEY заполнен — озвучка голосом доступна")
    else:
        item("warn", "ELEVENLABS_API_KEY пустой — нужен ТОЛЬКО для TTS-озвучки",
             "cp .env.example .env, вставь ключ с elevenlabs.io. "
             "Без него всё остальное работает.")

    # 4) Шрифты (для субтитров) — в комплекте
    header("4) Шрифты (для субтитров)")
    fonts = list((ROOT / "assets" / "fonts").glob("*.ttf")) if (ROOT / "assets" / "fonts").is_dir() else []
    if fonts:
        item("ok", f"Шрифты на месте ({len(fonts)} шт.) — субтитры отрисуются")
    else:
        item("warn", "Не нашёл шрифты в assets/fonts (обычно они в комплекте)")

    # 5) Твоё медиа
    header("5) Твоё медиа (что ты уже закинул)")
    raw = count_raw()
    music = count_media(ROOT / "assets" / "music", AUDIO_EXT)
    sounds = count_media(ROOT / "assets" / "sounds", AUDIO_EXT)
    broll = count_media(ROOT / "assets" / "broll", VIDEO_EXT)
    brand = count_media(ROOT / "assets" / "broll_brand", VIDEO_EXT)
    hooks = count_media(ROOT / "assets" / "hooks", VIDEO_EXT)
    ctas = count_media(ROOT / "assets" / "ctas", VIDEO_EXT)
    th = count_media(ROOT / "assets" / "talking_head_sources", VIDEO_EXT)
    scripts = len(list((ROOT / "scripts").glob("*.json"))) if (ROOT / "scripts").is_dir() else 0

    rows = [
        ("📥 raw/ — входящие (агент разложит)", raw),
        ("🎵 музыка        assets/music", music),
        ("🔊 звуки/SFX     assets/sounds", sounds),
        ("🎬 b-roll        assets/broll", broll),
        ("🛍  product b-roll assets/broll_brand", brand),
        ("🎯 хуки          assets/hooks", hooks),
        ("📣 CTA-концовки  assets/ctas", ctas),
        ("🗣  talking-head  assets/talking_head_sources", th),
        ("📝 сценарии      scripts/*.json", scripts),
    ]
    for label, n in rows:
        shown = (green(str(n)) if n else dim("0"))
        print(f"   {label:<42} {shown}")
    if raw:
        print(dim(f"\n   В raw/ лежит {raw} необработанн(ый/ых) файл(ов) — "
                  f"скажи агенту «разбери raw», и он разложит их по местам."))

    # 6) Что уже можно собрать
    header("6) Что ты уже можешь собрать")
    raw_vid = count_raw_video()
    total_vid = broll + brand + th + raw_vid

    def route(ok: bool, name: str, need: str) -> None:
        if ok:
            item("ok", bold(name) + " — готов к сборке")
        else:
            item("bad", name, "нужно: " + need)

    route(hooks >= 1 and tts,
          "Стандартный рилс (build)",
          "хук-клип в assets/hooks + ключ ElevenLabs (озвучка). "
          "Музыка и b-roll — по желанию (можно бросить в raw/).")
    if hooks >= 1 and not tts:
        item("warn", "  …хук есть, но без ключа TTS не будет озвучки голосом")
    route(th >= 1, "Talking-head (чистка живого видео)",
          "хотя бы одно видео «ты на камеру» (брось в raw/ или assets/talking_head_sources)")
    route(th >= 1 and brand >= 1, "Ref-style монтаж",
          "talking-head видео + product b-roll (assets/broll_brand)")
    route(total_vid >= 3, "3-strip (3 горизонтальных клипа в кадре)",
          f"хотя бы 3 горизонтальных клипа (сейчас доступно {total_vid}). "
          "Брось в raw/ — агент проверит ориентацию глазами.")

    # ── Итог ────────────────────────────────────────────────────────────
    print()
    print(bold("─" * 64))
    if blockers:
        print(bold(red("  Готово к работе: ПОКА НЕТ")))
        nxt = {
            "ffmpeg": "brew install ffmpeg",
            "ffprobe": "brew install ffmpeg",
            "python": "поставить Python ≥ 3.10 (например 3.12) — python.org или brew",
            "venv": "python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt",
            "deps": ".venv/bin/pip install -r requirements.txt",
        }
        print()
        print("  " + bold("Проще всего — попроси агента:") + " «установи всё, что нужно».")
        print(dim("  Он сам всё скачает, поставит и сообщит, когда можно запускать —"))
        print(dim("  вводить команды в терминал не придётся."))
        print()
        first = blockers[0]
        print(dim("  Если хочешь вручную:  " + nxt.get(first, "см. пункты с ✗ выше")))
        print(dim("  потом снова проверь:  python3 check_setup.py"))
    else:
        print(bold(green("  Готово к работе: ДА ✓")))
        any_media = any((raw, music, sounds, broll, brand, hooks, ctas, th))
        if not any_media:
            print("  Движок настроен. Осталось добавить медиа:")
            print(dim("  брось видео/музыку/звуки в папку  raw/  и скажи агенту,"))
            print(dim("  что хочешь собрать — он разложит файлы и сделает видео."))
        else:
            print("  Можно собирать. Брось новые файлы в  raw/  и дай агенту задачу,")
            print(dim("  либо см. готовые маршруты в пункте 6 выше."))
        print(dim("  Подробный онбординг для человека:  НАЧНИ_ЗДЕСЬ.md"))
    print(bold("─" * 64))
    print()

    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
