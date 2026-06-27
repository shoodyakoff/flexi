"""Seed stubs for the public repo.

The real seed data (personal challenge / soprovod / explainer scripts) is NOT
shipped here on purpose — it is local-only content that lives in the gitignored
SQLite library (``data/scripts.sqlite``), not in tracked source.

These stubs keep the ``python -m src.script_app`` CLI surface intact (the
``seed-*`` commands exist and are no-ops) so the library viewer and importer work
without dragging private content into a public repository. To populate the
library locally, import episodes directly via ``db.upsert_seed`` / ``upsert_seeds``
against ``data/scripts.sqlite``.
"""
from __future__ import annotations

from pathlib import Path

from .db import (  # re-exported for parity with upstream API
    BacklogSeed,
    ScriptSeed,
    connect,
    upsert_backlog_seeds,
    upsert_seeds,
)

__all__ = [
    "BacklogSeed",
    "ScriptSeed",
    "connect",
    "upsert_seeds",
    "upsert_backlog_seeds",
    "SOPROVOD_SEEDS",
    "EXPLAIN_WITH_IMAGES_SEEDS",
    "CHALLENGE_SEEDS",
    "CHALLENGE_BACKLOG_SEEDS",
    "seed_soprovod",
    "seed_explain_with_images",
    "seed_challenge",
    "seed_challenge_backlog",
]

# Personal seed payloads intentionally omitted from the public repo.
SOPROVOD_SEEDS: list[ScriptSeed] = []
EXPLAIN_WITH_IMAGES_SEEDS: list[ScriptSeed] = []
CHALLENGE_SEEDS: list[ScriptSeed] = []
CHALLENGE_BACKLOG_SEEDS: list[BacklogSeed] = []


def seed_soprovod(db_path: Path | str | None = None) -> int:
    return 0


def seed_explain_with_images(db_path: Path | str | None = None) -> int:
    return 0


def seed_challenge(db_path: Path | str | None = None) -> int:
    return 0


def seed_challenge_backlog(db_path: Path | str | None = None) -> int:
    return 0
