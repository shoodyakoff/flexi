#!/usr/bin/env python3
"""Тонкая обёртка над `python -m src.cli meme` для симметрии с прочими pipelines/.

Делегирует в мультикоманду `src.cli.app`, подставляя подкоманду `meme` первым
токеном после имени программы (иначе typer не найдёт команду). Все флаги
(`--pairs`, `--series`, `--faces`, `--plan`, `--faces-subset`, `--seed`) и
аргумент `source` пробрасываются как есть.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.cli import app  # noqa: E402

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "meme", *sys.argv[1:]]
    app()
