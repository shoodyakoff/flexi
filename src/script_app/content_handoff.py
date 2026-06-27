from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import CONTENT_INBOX_DIR, CONTENT_OUTBOX_DIR

SCHEMA_VERSION = "content-factory-card-v1"
TASK_SCHEMA_VERSION = "claqs-video-task-v1"
REQUIRED_FIELDS = ("channel", "format", "title", "status")
REQUIRED_TASK_FIELDS = ("channel", "format", "feature", "brief", "cta")


def write_content_factory_card(
    card: dict[str, Any],
    *,
    outbox_dir: Path = CONTENT_OUTBOX_DIR,
) -> Path:
    missing = [field for field in REQUIRED_FIELDS if not str(card.get(field) or "").strip()]
    if missing:
        raise ValueError(f"content card missing required fields: {', '.join(missing)}")

    normalized = {
        **card,
        "id": str(card.get("id") or _stable_id(card)),
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }
    outbox_dir.mkdir(parents=True, exist_ok=True)
    out_path = outbox_dir / f"{_safe_filename(str(normalized['id']))}.json"
    out_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return out_path


def write_content_factory_card_from_file(
    input_path: Path,
    *,
    outbox_dir: Path = CONTENT_OUTBOX_DIR,
) -> Path:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("content card input must be a JSON object")
    return write_content_factory_card(payload, outbox_dir=outbox_dir)


def write_content_factory_task_from_file(
    input_path: Path,
    *,
    inbox_dir: Path = CONTENT_INBOX_DIR,
) -> Path:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("content task input must be a JSON object")
    return write_content_factory_task(payload, inbox_dir=inbox_dir, imported_from=input_path)


def write_content_factory_task(
    task: dict[str, Any],
    *,
    inbox_dir: Path = CONTENT_INBOX_DIR,
    imported_from: Path | None = None,
) -> Path:
    missing = [field for field in REQUIRED_TASK_FIELDS if not str(task.get(field) or "").strip()]
    if missing:
        raise ValueError(f"content task missing required fields: {', '.join(missing)}")
    normalized = {
        **task,
        "id": str(task.get("id") or _stable_task_id(task)),
        "schema_version": TASK_SCHEMA_VERSION,
        "status": str(task.get("status") or "queued"),
        "imported_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source": {
            **(task.get("source") if isinstance(task.get("source"), dict) else {}),
            **({"imported_from": str(imported_from)} if imported_from else {}),
        },
    }
    inbox_dir.mkdir(parents=True, exist_ok=True)
    out_path = inbox_dir / f"{_safe_filename(str(normalized['id']))}.json"
    out_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return out_path


def _stable_id(card: dict[str, Any]) -> str:
    basis = "|".join(str(card.get(key) or "") for key in ("channel", "format", "title", "published_at"))
    return _safe_filename(basis)[:80] or "content-card"


def _stable_task_id(task: dict[str, Any]) -> str:
    basis = "|".join(str(task.get(key) or "") for key in ("channel", "format", "feature", "deadline", "brief"))
    return _safe_filename(basis)[:80] or "content-task"


def _safe_filename(value: str) -> str:
    normalized = value.lower().strip().replace("ё", "е")
    normalized = re.sub(r"[^\w\s-]", "", normalized, flags=re.UNICODE)
    normalized = re.sub(r"[\s_-]+", "-", normalized).strip("-")
    return normalized or "content-card"
