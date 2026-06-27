from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .paths import DEFAULT_DB_PATH

DIRECTION_OPTIONS = ("generated_video", "challenge_ai_income", "explain_with_images")
DIRECTIONS = set(DIRECTION_OPTIONS)
ITEM_TYPE_OPTIONS = (
    "generated_video",
    "challenge_episode",
    "image_explainer",
    "challenge_backlog",
)
SCRIPT_ITEM_TYPES = ("generated_video", "challenge_episode", "image_explainer")
LEGACY_DIRECTION_TO_ITEM_TYPE = {
    "generated_video": "generated_video",
    "challenge_ai_income": "challenge_episode",
    "explain_with_images": "image_explainer",
}
ITEM_TYPE_TO_LEGACY_DIRECTION = {
    value: key for key, value in LEGACY_DIRECTION_TO_ITEM_TYPE.items()
}
SCRIPT_STATUS_OPTIONS = ("idea", "script", "ready_to_shoot", "shot", "published")
SCRIPT_STATUSES = set(SCRIPT_STATUS_OPTIONS)
GENERATED_STATUSES = SCRIPT_STATUSES
BACKLOG_LINK_TYPE = "backlog_for_episode"

_MONTHS_RU = {
    "январь": 1,
    "января": 1,
    "февраль": 2,
    "февраля": 2,
    "март": 3,
    "марта": 3,
    "апрель": 4,
    "апреля": 4,
    "май": 5,
    "мая": 5,
    "июнь": 6,
    "июня": 6,
    "июль": 7,
    "июля": 7,
    "август": 8,
    "августа": 8,
    "сентябрь": 9,
    "сентября": 9,
    "октябрь": 10,
    "октября": 10,
    "ноябрь": 11,
    "ноября": 11,
    "декабрь": 12,
    "декабря": 12,
}


@dataclass(frozen=True)
class ScriptSeed:
    external_key: str
    direction: str
    title: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class BacklogSeed:
    external_key: str
    title: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class FieldSpec:
    source: str
    key: str
    field_type: str = "text"


SCRIPT_FIELD_SPECS = (
    FieldSpec("day_number", "day_number", "integer"),
    FieldSpec("raw_text", "raw_text"),
    FieldSpec("notes", "notes"),
    FieldSpec("funnel_stage", "funnel_stage"),
    FieldSpec("cta_type", "cta_type"),
    FieldSpec("keyword", "keyword"),
    FieldSpec("hook_text", "hook"),
    FieldSpec("voiceover_text_raw", "voiceover_raw"),
    FieldSpec("voiceover_text_marked", "voiceover_marked"),
    FieldSpec("cta_text", "cta"),
    FieldSpec("caption_text", "caption"),
    FieldSpec("pinned_comment", "pinned_comment"),
    FieldSpec("hook_asset_id", "hook_asset_id"),
    FieldSpec("cta_asset_id", "cta_asset_id"),
    FieldSpec("exported_script_path", "exported_script_path"),
    FieldSpec("episode_label", "episode_label"),
    FieldSpec("opening_line", "opening_line"),
    FieldSpec("script_text", "script"),
    FieldSpec("core_topic", "core_topic"),
    FieldSpec("business_stream", "business_stream"),
    FieldSpec("challenge_pillar", "challenge_pillar"),
    FieldSpec("hook_class", "hook_class"),
    FieldSpec("series_key", "series_key"),
    FieldSpec("storyboard_text", "storyboard"),
    FieldSpec("decisions_text", "decisions"),
    FieldSpec("replaced_draft_text", "replaced_draft"),
    FieldSpec("deferred_results_text", "deferred_results"),
    FieldSpec("format_notes", "format_notes"),
    FieldSpec("publish_channel", "publish_channel"),
    FieldSpec("shot_status", "shot_status"),
)
BACKLOG_FIELD_SPECS = (
    FieldSpec("category", "category"),
    FieldSpec("pillar", "challenge_pillar"),
    FieldSpec("business_stream", "business_stream"),
    FieldSpec("source", "source"),
    FieldSpec("suggested_hook_class", "hook_class"),
    FieldSpec("return_window", "return_window"),
    FieldSpec("due_date", "due_date", "date"),
    FieldSpec("notes", "notes"),
)
FIELD_TYPES = {
    spec.key: spec.field_type for spec in (*SCRIPT_FIELD_SPECS, *BACKLOG_FIELD_SPECS)
}
FIELD_ORDER = {
    spec.key: index
    for index, spec in enumerate((*SCRIPT_FIELD_SPECS, *BACKLOG_FIELD_SPECS), start=1)
}
FIELD_ALIASES = {
    "hook": ("hook_text",),
    "voiceover_raw": ("voiceover_text_raw",),
    "voiceover_marked": ("voiceover_text_marked",),
    "cta": ("cta_text",),
    "caption": ("caption_text",),
    "script": ("script_text",),
    "storyboard": ("storyboard_text",),
    "decisions": ("decisions_text",),
    "replaced_draft": ("replaced_draft_text",),
    "deferred_results": ("deferred_results_text",),
    "challenge_pillar": ("pillar",),
    "hook_class": ("suggested_hook_class",),
}
ROW_DEFAULTS: dict[str, Any] = {
    "day_number": None,
    "raw_text": "",
    "notes": "",
    "funnel_stage": None,
    "cta_type": None,
    "keyword": None,
    "hook": "",
    "hook_text": "",
    "voiceover_raw": "",
    "voiceover_text_raw": "",
    "voiceover_marked": "",
    "voiceover_text_marked": "",
    "cta": "",
    "cta_text": "",
    "caption": "",
    "caption_text": "",
    "pinned_comment": "",
    "hook_asset_id": None,
    "cta_asset_id": None,
    "exported_script_path": None,
    "episode_label": None,
    "opening_line": "",
    "script": "",
    "script_text": "",
    "core_topic": "",
    "business_stream": None,
    "challenge_pillar": "",
    "pillar": "",
    "hook_class": "",
    "suggested_hook_class": "",
    "series_key": "",
    "storyboard": "",
    "storyboard_text": "",
    "decisions": "",
    "decisions_text": "",
    "replaced_draft": "",
    "replaced_draft_text": "",
    "deferred_results": "",
    "deferred_results_text": "",
    "format_notes": "",
    "publish_channel": "instagram",
    "shot_status": "script_only",
    "category": "",
    "source": "manual",
    "return_window": "",
    "due_date": None,
    "source_episode_id": None,
    "source_episode_label": None,
    "source_day_number": None,
    "source_created_at": None,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def infer_backlog_due_date(return_window: str, base_date: date | None = None) -> str | None:
    text = return_window.strip().lower()
    if not text:
        return None
    base = base_date or date.today()

    for month_name, month in _MONTHS_RU.items():
        match = re.search(rf"\b{month_name}\s+(20\d{{2}})\b", text)
        if match:
            return f"{int(match.group(1)):04d}-{month:02d}-01"

    weeks = re.search(r"через\s+(\d+)(?:\s*[-–]\s*\d+)?\s+нед", text)
    if weeks:
        return (base + timedelta(weeks=int(weeks.group(1)))).isoformat()

    days = re.search(r"через\s+(\d+)(?:\s*[-–]\s*\d+)?\s+д", text)
    if days:
        return (base + timedelta(days=int(days.group(1)))).isoformat()

    recurring_day = re.search(r"каждый\s+(\d+)(?:-\S*)?\s+день", text)
    if recurring_day:
        return (base + timedelta(days=int(recurring_day.group(1)))).isoformat()

    months = re.search(r"через\s+(\d+)(?:\s*[-–]\s*\d+)?\s+мес", text)
    if months:
        month_offset = int(months.group(1))
        month_index = base.month - 1 + month_offset
        year = base.year + month_index // 12
        month = month_index % 12 + 1
        day = min(base.day, 28)
        return date(year, month, day).isoformat()

    return None


def _date_from_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _source_episode_date(
    source_episode_id: Any,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> date | None:
    if not source_episode_id:
        return None
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT created_at FROM script_ideas WHERE id = ?",
            (source_episode_id,),
        ).fetchone()
    return _date_from_iso(row["created_at"]) if row else None


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS script_ideas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_key TEXT UNIQUE,
                direction TEXT NOT NULL CHECK (direction IN ('generated_video', 'challenge_ai_income', 'explain_with_images')),
                title TEXT NOT NULL,
                day_number INTEGER,
                status TEXT NOT NULL DEFAULT 'idea',
                raw_text TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',

                funnel_stage TEXT,
                cta_type TEXT,
                keyword TEXT,
                hook_text TEXT NOT NULL DEFAULT '',
                voiceover_text_raw TEXT NOT NULL DEFAULT '',
                voiceover_text_marked TEXT NOT NULL DEFAULT '',
                cta_text TEXT NOT NULL DEFAULT '',
                caption_text TEXT NOT NULL DEFAULT '',
                pinned_comment TEXT NOT NULL DEFAULT '',
                hook_asset_id TEXT,
                cta_asset_id TEXT,
                exported_script_path TEXT,

                episode_label TEXT,
                opening_line TEXT NOT NULL DEFAULT '',
                script_text TEXT NOT NULL DEFAULT '',
                core_topic TEXT NOT NULL DEFAULT '',
                business_stream TEXT,
                challenge_pillar TEXT NOT NULL DEFAULT '',
                hook_class TEXT NOT NULL DEFAULT '',
                series_key TEXT NOT NULL DEFAULT '',
                storyboard_text TEXT NOT NULL DEFAULT '',
                decisions_text TEXT NOT NULL DEFAULT '',
                replaced_draft_text TEXT NOT NULL DEFAULT '',
                deferred_results_text TEXT NOT NULL DEFAULT '',
                format_notes TEXT NOT NULL DEFAULT '',
                publish_channel TEXT NOT NULL DEFAULT 'instagram',
                shot_status TEXT NOT NULL DEFAULT 'script_only',

                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS prompt_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                direction TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS challenge_backlog_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_key TEXT UNIQUE,
                title TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                pillar TEXT NOT NULL DEFAULT '',
                business_stream TEXT,
                status TEXT NOT NULL DEFAULT 'idea',
                source TEXT NOT NULL DEFAULT 'manual',
                source_episode_id INTEGER,
                suggested_hook_class TEXT NOT NULL DEFAULT '',
                return_window TEXT NOT NULL DEFAULT '',
                due_date TEXT,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (source_episode_id) REFERENCES script_ideas(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_challenge_backlog_status
                ON challenge_backlog_items(status);
            CREATE INDEX IF NOT EXISTS idx_challenge_backlog_category
                ON challenge_backlog_items(category);
            CREATE INDEX IF NOT EXISTS idx_challenge_backlog_source_episode
                ON challenge_backlog_items(source_episode_id);
            """
        )
        _migrate_script_ideas_direction_constraint(conn)
        _ensure_script_idea_indexes(conn)
        _ensure_columns(
            conn,
            "script_ideas",
            {
                "caption_text": "TEXT NOT NULL DEFAULT ''",
                "pinned_comment": "TEXT NOT NULL DEFAULT ''",
                "challenge_pillar": "TEXT NOT NULL DEFAULT ''",
                "hook_class": "TEXT NOT NULL DEFAULT ''",
                "series_key": "TEXT NOT NULL DEFAULT ''",
                "storyboard_text": "TEXT NOT NULL DEFAULT ''",
                "decisions_text": "TEXT NOT NULL DEFAULT ''",
                "replaced_draft_text": "TEXT NOT NULL DEFAULT ''",
                "deferred_results_text": "TEXT NOT NULL DEFAULT ''",
                "format_notes": "TEXT NOT NULL DEFAULT ''",
            },
        )
        _normalize_existing_statuses(conn)
        _ensure_library_tables(conn)
        _sync_library_from_legacy(conn)


def _ensure_script_idea_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_script_ideas_direction
            ON script_ideas(direction);
        CREATE INDEX IF NOT EXISTS idx_script_ideas_status
            ON script_ideas(status);
        CREATE INDEX IF NOT EXISTS idx_script_ideas_day
            ON script_ideas(direction, day_number);
        """
    )


def _ensure_library_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS library_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_key TEXT UNIQUE,
            item_type TEXT NOT NULL CHECK (item_type IN ('generated_video', 'challenge_episode', 'image_explainer', 'challenge_backlog')),
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'idea',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS library_item_fields (
            item_id INTEGER NOT NULL,
            field_key TEXT NOT NULL,
            field_type TEXT NOT NULL DEFAULT 'text',
            value TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (item_id, field_key),
            FOREIGN KEY (item_id) REFERENCES library_items(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS library_item_links (
            source_item_id INTEGER NOT NULL,
            target_item_id INTEGER NOT NULL,
            link_type TEXT NOT NULL,
            PRIMARY KEY (source_item_id, target_item_id, link_type),
            FOREIGN KEY (source_item_id) REFERENCES library_items(id) ON DELETE CASCADE,
            FOREIGN KEY (target_item_id) REFERENCES library_items(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS library_legacy_map (
            legacy_table TEXT NOT NULL,
            legacy_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL UNIQUE,
            PRIMARY KEY (legacy_table, legacy_id),
            FOREIGN KEY (item_id) REFERENCES library_items(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_library_items_type
            ON library_items(item_type);
        CREATE INDEX IF NOT EXISTS idx_library_items_status
            ON library_items(status);
        CREATE INDEX IF NOT EXISTS idx_library_item_fields_key
            ON library_item_fields(field_key);
        CREATE INDEX IF NOT EXISTS idx_library_links_source
            ON library_item_links(source_item_id, link_type);
        CREATE INDEX IF NOT EXISTS idx_library_links_target
            ON library_item_links(target_item_id, link_type);
        """
    )


def _migrate_script_ideas_direction_constraint(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'script_ideas'"
    ).fetchone()
    if row is None or "explain_with_images" in str(row["sql"]):
        return

    conn.execute("ALTER TABLE script_ideas RENAME TO script_ideas_legacy")
    conn.executescript(
        """
        CREATE TABLE script_ideas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_key TEXT UNIQUE,
            direction TEXT NOT NULL CHECK (direction IN ('generated_video', 'challenge_ai_income', 'explain_with_images')),
            title TEXT NOT NULL,
            day_number INTEGER,
            status TEXT NOT NULL DEFAULT 'idea',
            raw_text TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',

            funnel_stage TEXT,
            cta_type TEXT,
            keyword TEXT,
            hook_text TEXT NOT NULL DEFAULT '',
            voiceover_text_raw TEXT NOT NULL DEFAULT '',
            voiceover_text_marked TEXT NOT NULL DEFAULT '',
            cta_text TEXT NOT NULL DEFAULT '',
            caption_text TEXT NOT NULL DEFAULT '',
            pinned_comment TEXT NOT NULL DEFAULT '',
            hook_asset_id TEXT,
            cta_asset_id TEXT,
            exported_script_path TEXT,

            episode_label TEXT,
            opening_line TEXT NOT NULL DEFAULT '',
            script_text TEXT NOT NULL DEFAULT '',
            core_topic TEXT NOT NULL DEFAULT '',
            business_stream TEXT,
            challenge_pillar TEXT NOT NULL DEFAULT '',
            hook_class TEXT NOT NULL DEFAULT '',
            series_key TEXT NOT NULL DEFAULT '',
            storyboard_text TEXT NOT NULL DEFAULT '',
            decisions_text TEXT NOT NULL DEFAULT '',
            replaced_draft_text TEXT NOT NULL DEFAULT '',
            deferred_results_text TEXT NOT NULL DEFAULT '',
            format_notes TEXT NOT NULL DEFAULT '',
            publish_channel TEXT NOT NULL DEFAULT 'instagram',
            shot_status TEXT NOT NULL DEFAULT 'script_only',

            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    legacy_columns = {
        item["name"] for item in conn.execute("PRAGMA table_info(script_ideas_legacy)")
    }
    current_columns = {
        item["name"] for item in conn.execute("PRAGMA table_info(script_ideas)")
    }
    columns = [column for column in current_columns if column in legacy_columns]
    column_sql = ", ".join(columns)
    conn.execute(
        f"""
        INSERT INTO script_ideas ({column_sql})
        SELECT {column_sql}
        FROM script_ideas_legacy
        """
    )
    conn.execute("DROP TABLE script_ideas_legacy")


def canonical_script_status(status: Any, shot_status: Any = None) -> str:
    status_value = str(status or "").strip()
    shot_value = str(shot_status or "").strip()
    if status_value == "published" or shot_value == "posted":
        return "published"
    if status_value == "shot" or shot_value in {"shot", "edited"}:
        return "shot"
    if status_value in {"ready_to_shoot", "ready", "exported"}:
        return "ready_to_shoot"
    if status_value in {"script", "draft", "scripted"}:
        return "script"
    return "idea"


def shot_status_for_script_status(status: Any) -> str:
    status_value = canonical_script_status(status)
    if status_value == "published":
        return "posted"
    if status_value == "shot":
        return "shot"
    return "script_only"


def normalize_item_type(value: Any) -> str:
    raw = str(value or "generated_video").strip()
    if raw in LEGACY_DIRECTION_TO_ITEM_TYPE:
        return LEGACY_DIRECTION_TO_ITEM_TYPE[raw]
    if raw in ITEM_TYPE_OPTIONS:
        return raw
    return "generated_video"


def legacy_direction_for_item_type(item_type: Any) -> str:
    normalized = normalize_item_type(item_type)
    return ITEM_TYPE_TO_LEGACY_DIRECTION.get(normalized, normalized)


def _normalize_existing_statuses(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE script_ideas
        SET
            status = CASE
                WHEN status = 'published' OR shot_status = 'posted' THEN 'published'
                WHEN status = 'shot' OR shot_status IN ('shot', 'edited') THEN 'shot'
                WHEN status IN ('ready_to_shoot', 'ready', 'exported') THEN 'ready_to_shoot'
                WHEN status IN ('script', 'draft', 'scripted') THEN 'script'
                ELSE 'idea'
            END,
            shot_status = CASE
                WHEN status = 'published' OR shot_status = 'posted' THEN 'posted'
                WHEN status = 'shot' OR shot_status IN ('shot', 'edited') THEN 'shot'
                ELSE 'script_only'
            END
        WHERE status NOT IN ('idea', 'script', 'ready_to_shoot', 'shot', 'published')
            OR shot_status IN ('shot', 'edited', 'posted')
        """
    )
    conn.execute(
        """
        UPDATE challenge_backlog_items
        SET status = CASE
            WHEN status = 'published' THEN 'published'
            WHEN status = 'shot' THEN 'shot'
            WHEN status IN ('ready_to_shoot', 'ready', 'exported') THEN 'ready_to_shoot'
            WHEN status IN ('script', 'scripted', 'draft') THEN 'script'
            ELSE 'idea'
        END
        WHERE status NOT IN ('idea', 'script', 'ready_to_shoot', 'shot', 'published')
        """
    )


def _canonicalize_script_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "status" not in payload and "shot_status" not in payload:
        return payload
    next_payload = dict(payload)
    status = canonical_script_status(next_payload.get("status"), next_payload.get("shot_status"))
    next_payload["status"] = status
    next_payload["shot_status"] = shot_status_for_script_status(status)
    return next_payload


def _canonicalize_backlog_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "status" not in payload:
        return payload
    return {**payload, "status": canonical_script_status(payload["status"])}


def _ensure_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: dict[str, str],
) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _insert_columns(payload: dict[str, Any]) -> tuple[str, str, list[Any]]:
    columns = list(payload)
    placeholders = ", ".join("?" for _ in columns)
    return ", ".join(columns), placeholders, [payload[column] for column in columns]


def _storage_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _coerce_field_value(field_key: str, value: str) -> Any:
    if value == "":
        return ROW_DEFAULTS.get(field_key, "")
    if FIELD_TYPES.get(field_key) == "integer":
        try:
            return int(value)
        except ValueError:
            return None
    return value


def _field_payload(
    payload: dict[str, Any],
    specs: Iterable[FieldSpec],
) -> dict[str, tuple[Any, str, int]]:
    fields: dict[str, tuple[Any, str, int]] = {}
    for index, spec in enumerate(specs, start=1):
        if spec.source in payload:
            value = payload[spec.source]
        elif spec.key in payload:
            value = payload[spec.key]
        else:
            continue
        fields[spec.key] = (value, spec.field_type, FIELD_ORDER.get(spec.key, index))
    return fields


def _upsert_fields(
    conn: sqlite3.Connection,
    item_id: int,
    fields: dict[str, tuple[Any, str, int]],
) -> None:
    for field_key, (value, field_type, sort_order) in fields.items():
        conn.execute(
            """
            INSERT INTO library_item_fields (item_id, field_key, field_type, value, sort_order)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(item_id, field_key) DO UPDATE SET
                field_type = excluded.field_type,
                value = excluded.value,
                sort_order = excluded.sort_order
            """,
            (item_id, field_key, field_type, _storage_value(value), sort_order),
        )


def _mapped_item_id(
    conn: sqlite3.Connection,
    legacy_table: str,
    legacy_id: Any,
) -> int | None:
    if legacy_id is None:
        return None
    row = conn.execute(
        """
        SELECT item_id
        FROM library_legacy_map
        WHERE legacy_table = ? AND legacy_id = ?
        """,
        (legacy_table, legacy_id),
    ).fetchone()
    return int(row["item_id"]) if row else None


def _item_id_by_external_key(conn: sqlite3.Connection, external_key: Any) -> int | None:
    if not external_key:
        return None
    row = conn.execute(
        "SELECT id FROM library_items WHERE external_key = ?",
        (external_key,),
    ).fetchone()
    return int(row["id"]) if row else None


def _library_updated_at(conn: sqlite3.Connection, item_id: int) -> str | None:
    row = conn.execute(
        "SELECT updated_at FROM library_items WHERE id = ?",
        (item_id,),
    ).fetchone()
    return str(row["updated_at"]) if row else None


def _should_sync_legacy(conn: sqlite3.Connection, item_id: int | None, legacy_updated_at: str) -> bool:
    if item_id is None:
        return True
    library_updated_at = _library_updated_at(conn, item_id)
    if not library_updated_at:
        return True
    field_count = conn.execute(
        "SELECT COUNT(*) FROM library_item_fields WHERE item_id = ?",
        (item_id,),
    ).fetchone()[0]
    return field_count == 0 or legacy_updated_at > library_updated_at


def _sync_library_from_legacy(conn: sqlite3.Connection) -> None:
    for row in conn.execute("SELECT * FROM script_ideas ORDER BY id ASC"):
        _upsert_library_item_from_script_row(conn, row)
    for row in conn.execute("SELECT * FROM challenge_backlog_items ORDER BY id ASC"):
        _upsert_library_item_from_backlog_row(conn, row)


def _upsert_library_item_from_script_row(conn: sqlite3.Connection, row: sqlite3.Row) -> int:
    item_type = normalize_item_type(row["direction"])
    item_id = _mapped_item_id(conn, "script_ideas", row["id"])
    item_id = item_id or _item_id_by_external_key(conn, row["external_key"])
    if not _should_sync_legacy(conn, item_id, row["updated_at"]):
        return int(item_id)

    if item_id is None:
        cursor = conn.execute(
            """
            INSERT INTO library_items (external_key, item_type, title, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                row["external_key"],
                item_type,
                row["title"],
                canonical_script_status(row["status"], row["shot_status"]),
                row["created_at"],
                row["updated_at"],
            ),
        )
        item_id = int(cursor.lastrowid)
    else:
        conn.execute(
            """
            UPDATE library_items
            SET external_key = ?, item_type = ?, title = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                row["external_key"],
                item_type,
                row["title"],
                canonical_script_status(row["status"], row["shot_status"]),
                row["updated_at"],
                item_id,
            ),
        )
    conn.execute(
        """
        INSERT OR IGNORE INTO library_legacy_map (legacy_table, legacy_id, item_id)
        VALUES ('script_ideas', ?, ?)
        """,
        (row["id"], item_id),
    )
    _upsert_fields(conn, item_id, _field_payload(dict(row), SCRIPT_FIELD_SPECS))
    return int(item_id)


def _upsert_library_item_from_backlog_row(conn: sqlite3.Connection, row: sqlite3.Row) -> int:
    item_id = _mapped_item_id(conn, "challenge_backlog_items", row["id"])
    item_id = item_id or _item_id_by_external_key(conn, row["external_key"])
    if not _should_sync_legacy(conn, item_id, row["updated_at"]):
        return int(item_id)

    if item_id is None:
        cursor = conn.execute(
            """
            INSERT INTO library_items (external_key, item_type, title, status, created_at, updated_at)
            VALUES (?, 'challenge_backlog', ?, ?, ?, ?)
            """,
            (
                row["external_key"],
                row["title"],
                canonical_script_status(row["status"]),
                row["created_at"],
                row["updated_at"],
            ),
        )
        item_id = int(cursor.lastrowid)
    else:
        conn.execute(
            """
            UPDATE library_items
            SET external_key = ?, item_type = 'challenge_backlog', title = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                row["external_key"],
                row["title"],
                canonical_script_status(row["status"]),
                row["updated_at"],
                item_id,
            ),
        )
    conn.execute(
        """
        INSERT OR IGNORE INTO library_legacy_map (legacy_table, legacy_id, item_id)
        VALUES ('challenge_backlog_items', ?, ?)
        """,
        (row["id"], item_id),
    )
    _upsert_fields(conn, item_id, _field_payload(dict(row), BACKLOG_FIELD_SPECS))
    _replace_backlog_source_link(conn, item_id, row["source_episode_id"])
    return int(item_id)


def _resolve_script_item_id(conn: sqlite3.Connection, source_episode_id: Any) -> int | None:
    if not source_episode_id:
        return None
    item = conn.execute(
        """
        SELECT id, item_type
        FROM library_items
        WHERE id = ?
        """,
        (source_episode_id,),
    ).fetchone()
    if item and item["item_type"] in SCRIPT_ITEM_TYPES:
        return int(item["id"])
    return _mapped_item_id(conn, "script_ideas", source_episode_id)


def _replace_backlog_source_link(
    conn: sqlite3.Connection,
    backlog_item_id: int,
    source_episode_id: Any,
) -> None:
    conn.execute(
        """
        DELETE FROM library_item_links
        WHERE target_item_id = ? AND link_type = ?
        """,
        (backlog_item_id, BACKLOG_LINK_TYPE),
    )
    source_item_id = _resolve_script_item_id(conn, source_episode_id)
    if source_item_id:
        conn.execute(
            """
            INSERT OR IGNORE INTO library_item_links (source_item_id, target_item_id, link_type)
            VALUES (?, ?, ?)
            """,
            (source_item_id, backlog_item_id, BACKLOG_LINK_TYPE),
        )


def _fields_by_item_id(
    conn: sqlite3.Connection,
    item_ids: list[int],
) -> dict[int, dict[str, Any]]:
    if not item_ids:
        return {}
    placeholders = ", ".join("?" for _ in item_ids)
    fields: dict[int, dict[str, Any]] = {item_id: {} for item_id in item_ids}
    for row in conn.execute(
        f"""
        SELECT item_id, field_key, field_type, value
        FROM library_item_fields
        WHERE item_id IN ({placeholders})
        ORDER BY sort_order ASC
        """,
        item_ids,
    ):
        fields[int(row["item_id"])][row["field_key"]] = _coerce_field_value(
            row["field_key"],
            row["value"],
        )
    return fields


def _compose_row(
    conn: sqlite3.Connection,
    item: sqlite3.Row,
    fields: dict[str, Any],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        **ROW_DEFAULTS,
        "id": int(item["id"]),
        "external_key": item["external_key"],
        "item_type": item["item_type"],
        "direction": legacy_direction_for_item_type(item["item_type"]),
        "title": item["title"],
        "status": item["status"],
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }
    for field_key, value in fields.items():
        row[field_key] = value
        for alias in FIELD_ALIASES.get(field_key, ()):
            row[alias] = value
    if row["item_type"] == "challenge_backlog":
        _attach_backlog_source(conn, row)
    return row


def _attach_backlog_source(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    source = conn.execute(
        """
        SELECT source_item_id
        FROM library_item_links
        WHERE target_item_id = ? AND link_type = ?
        """,
        (row["id"], BACKLOG_LINK_TYPE),
    ).fetchone()
    if source is None:
        return
    source_id = int(source["source_item_id"])
    item = conn.execute("SELECT * FROM library_items WHERE id = ?", (source_id,)).fetchone()
    if item is None:
        return
    fields = _fields_by_item_id(conn, [source_id]).get(source_id, {})
    row["source_episode_id"] = source_id
    row["source_episode_label"] = fields.get("episode_label") or item["title"]
    row["source_day_number"] = fields.get("day_number")
    row["source_created_at"] = item["created_at"]


def _library_rows(
    *,
    item_type: str | None = None,
    item_types: tuple[str, ...] | None = None,
    status: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_db(db_path)
    clauses: list[str] = []
    params: list[Any] = []
    if item_type:
        clauses.append("item_type = ?")
        params.append(normalize_item_type(item_type))
    if item_types:
        placeholders = ", ".join("?" for _ in item_types)
        clauses.append(f"item_type IN ({placeholders})")
        params.extend(item_types)
    if status:
        clauses.append("status = ?")
        params.append(canonical_script_status(status))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect(db_path) as conn:
        items = list(
            conn.execute(
                f"""
                SELECT *
                FROM library_items
                {where}
                """,
                params,
            )
        )
        fields = _fields_by_item_id(conn, [int(item["id"]) for item in items])
        return [_compose_row(conn, item, fields.get(int(item["id"]), {})) for item in items]


def _query_matches(row: dict[str, Any], query: str | None, field_names: tuple[str, ...]) -> bool:
    if not query:
        return True
    needle = query.lower()
    haystack = " ".join(str(row.get(name) or "") for name in ("title", *field_names)).lower()
    return needle in haystack


def _script_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    if row["item_type"] == "generated_video" and row["title"].startswith("РОЛИК №"):
        match = re.search(r"№(\d+)", row["title"])
        return (0, int(match.group(1)) if match else 9999, row["id"])
    if row["item_type"] == "challenge_episode":
        return (1, row["day_number"] or 9999, row["id"])
    return (2, "", "", row["updated_at"], row["id"])


def _backlog_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    status_rank = {
        "idea": 0,
        "script": 1,
        "ready_to_shoot": 2,
        "shot": 3,
        "published": 4,
    }
    return (
        status_rank.get(row["status"], 9),
        row["due_date"] or "9999-12-31",
        row["id"],
    )


def upsert_seed(seed: ScriptSeed, db_path: Path | str = DEFAULT_DB_PATH) -> int:
    init_db(db_path)
    now = utc_now()
    payload = {
        "external_key": seed.external_key,
        "direction": seed.direction,
        "title": seed.title,
        "created_at": now,
        "updated_at": now,
        **seed.payload,
    }
    payload = _canonicalize_script_payload(payload)
    columns, placeholders, values = _insert_columns(payload)
    update_columns = [
        column
        for column in payload
        if column not in {"id", "external_key", "created_at"}
    ]
    update_sql = ", ".join(f"{column}=excluded.{column}" for column in update_columns)
    with connect(db_path) as conn:
        conn.execute(
            f"""
            INSERT INTO script_ideas ({columns})
            VALUES ({placeholders})
            ON CONFLICT(external_key) DO UPDATE SET {update_sql}
            """,
            values,
        )
        row = conn.execute(
            "SELECT * FROM script_ideas WHERE external_key = ?",
            (seed.external_key,),
        ).fetchone()
        return _upsert_library_item_from_script_row(conn, row)


def upsert_seeds(seeds: Iterable[ScriptSeed], db_path: Path | str = DEFAULT_DB_PATH) -> int:
    count = 0
    for seed in seeds:
        upsert_seed(seed, db_path)
        count += 1
    return count


def upsert_backlog_seed(seed: BacklogSeed, db_path: Path | str = DEFAULT_DB_PATH) -> int:
    init_db(db_path)
    now = utc_now()
    payload = {
        "external_key": seed.external_key,
        "created_at": now,
        "updated_at": now,
        **seed.payload,
    }
    payload.setdefault("title", seed.title)
    payload = _canonicalize_backlog_payload(payload)
    if not payload.get("due_date"):
        payload["due_date"] = infer_backlog_due_date(
            str(payload.get("return_window", "")),
            _source_episode_date(payload.get("source_episode_id"), db_path),
        )
    columns, placeholders, values = _insert_columns(payload)
    update_columns = [
        column
        for column in payload
        if column not in {"id", "external_key", "created_at"}
    ]
    update_sql = ", ".join(f"{column}=excluded.{column}" for column in update_columns)
    with connect(db_path) as conn:
        conn.execute(
            f"""
            INSERT INTO challenge_backlog_items ({columns})
            VALUES ({placeholders})
            ON CONFLICT(external_key) DO UPDATE SET {update_sql}
            """,
            values,
        )
        row = conn.execute(
            "SELECT * FROM challenge_backlog_items WHERE external_key = ?",
            (seed.external_key,),
        ).fetchone()
        return _upsert_library_item_from_backlog_row(conn, row)


def upsert_backlog_seeds(
    seeds: Iterable[BacklogSeed],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> int:
    count = 0
    for seed in seeds:
        upsert_backlog_seed(seed, db_path)
        count += 1
    return count


def list_scripts(
    *,
    direction: str | None = None,
    item_type: str | None = None,
    status: str | None = None,
    query: str | None = None,
    funnel_stage: str | None = None,
    cta_type: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    normalized_type = normalize_item_type(item_type or direction) if (item_type or direction) else None
    item_types = None if normalized_type else SCRIPT_ITEM_TYPES
    rows = _library_rows(
        item_type=normalized_type,
        item_types=item_types,
        status=status or None,
        db_path=db_path,
    )
    rows = [
        row
        for row in rows
        if row["item_type"] != "challenge_backlog"
        and (not funnel_stage or row["funnel_stage"] == funnel_stage)
        and (not cta_type or row["cta_type"] == cta_type)
        and _query_matches(
            row,
            query,
            (
                "hook",
                "voiceover_raw",
                "voiceover_marked",
                "cta",
                "caption",
                "pinned_comment",
                "script",
                "storyboard",
            ),
        )
    ]
    return sorted(rows, key=_script_sort_key)


def list_challenge_backlog(
    *,
    status: str | None = None,
    category: str | None = None,
    query: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    rows = _library_rows(
        item_type="challenge_backlog",
        status=status or None,
        db_path=db_path,
    )
    rows = [
        row
        for row in rows
        if (not category or row["category"] == category)
        and _query_matches(row, query, ("notes", "return_window"))
    ]
    return sorted(rows, key=_backlog_sort_key)


def list_challenge_backlog_for_episode(
    source_episode_id: int,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_db(db_path)
    with connect(db_path) as conn:
        links = conn.execute(
            """
            SELECT target_item_id
            FROM library_item_links
            WHERE source_item_id = ? AND link_type = ?
            """,
            (source_episode_id, BACKLOG_LINK_TYPE),
        ).fetchall()
        target_ids = [int(link["target_item_id"]) for link in links]
        if not target_ids:
            return []
        placeholders = ", ".join("?" for _ in target_ids)
        items = list(
            conn.execute(
                f"""
                SELECT *
                FROM library_items
                WHERE id IN ({placeholders}) AND item_type = 'challenge_backlog'
                """,
                target_ids,
            )
        )
        fields = _fields_by_item_id(conn, [int(item["id"]) for item in items])
        rows = [_compose_row(conn, item, fields.get(int(item["id"]), {})) for item in items]
    return sorted(rows, key=_backlog_sort_key)


def get_challenge_backlog_item(
    item_id: int,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    row = get_library_item(item_id, db_path)
    if row["item_type"] != "challenge_backlog":
        raise KeyError(f"challenge backlog item not found: {item_id}")
    return row


def get_library_item(
    item_id: int,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    init_db(db_path)
    with connect(db_path) as conn:
        item = conn.execute("SELECT * FROM library_items WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            raise KeyError(f"library item not found: {item_id}")
        fields = _fields_by_item_id(conn, [item_id]).get(item_id, {})
        return _compose_row(conn, item, fields)


def create_challenge_backlog_item(
    payload: dict[str, Any],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> int:
    return create_library_item("challenge_backlog", payload, db_path)


def create_library_item(
    item_type: str,
    payload: dict[str, Any],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> int:
    init_db(db_path)
    normalized_type = normalize_item_type(item_type)
    now = utc_now()
    status = canonical_script_status(payload.get("status", "script"))
    fields = (
        _field_payload(payload, BACKLOG_FIELD_SPECS)
        if normalized_type == "challenge_backlog"
        else _field_payload(
            {
                **payload,
                "shot_status": payload.get("shot_status", shot_status_for_script_status(status)),
            },
            SCRIPT_FIELD_SPECS,
        )
    )
    with connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO library_items (external_key, item_type, title, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("external_key"),
                normalized_type,
                str(payload.get("title") or "Новый элемент библиотеки"),
                status,
                now,
                now,
            ),
        )
        item_id = int(cursor.lastrowid)
        _upsert_fields(conn, item_id, fields)
        if normalized_type == "challenge_backlog":
            _replace_backlog_source_link(conn, item_id, payload.get("source_episode_id"))
        return item_id


def update_challenge_backlog_item(
    item_id: int,
    payload: dict[str, Any],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    update_library_item(item_id, payload, db_path)


def update_library_item(
    item_id: int,
    payload: dict[str, Any],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    init_db(db_path)
    row = get_library_item(item_id, db_path)
    now = utc_now()
    with connect(db_path) as conn:
        updates: dict[str, Any] = {"updated_at": now}
        if "title" in payload:
            updates["title"] = payload["title"]
        if "status" in payload:
            updates["status"] = canonical_script_status(payload["status"])
        assignments = ", ".join(f"{column} = ?" for column in updates)
        values = [*updates.values(), item_id]
        cursor = conn.execute(
            f"UPDATE library_items SET {assignments} WHERE id = ?",
            values,
        )
        if cursor.rowcount == 0:
            raise KeyError(f"library item not found: {item_id}")

        if row["item_type"] == "challenge_backlog":
            fields = _field_payload(payload, BACKLOG_FIELD_SPECS)
            if "source_episode_id" in payload:
                _replace_backlog_source_link(conn, item_id, payload.get("source_episode_id"))
        else:
            next_payload = dict(payload)
            if "status" in updates and "shot_status" not in next_payload:
                next_payload["shot_status"] = shot_status_for_script_status(updates["status"])
            fields = _field_payload(next_payload, SCRIPT_FIELD_SPECS)
        _upsert_fields(conn, item_id, fields)


def get_script(script_id: int, db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, Any]:
    row = get_library_item(script_id, db_path)
    if row["item_type"] == "challenge_backlog":
        raise KeyError(f"script not found: {script_id}")
    return row


def create_script(payload: dict[str, Any], db_path: Path | str = DEFAULT_DB_PATH) -> int:
    item_type = normalize_item_type(payload.get("item_type") or payload.get("direction"))
    payload = _canonicalize_script_payload(payload)
    return create_library_item(item_type, payload, db_path)


def update_script(script_id: int, payload: dict[str, Any], db_path: Path | str = DEFAULT_DB_PATH) -> None:
    payload = _canonicalize_script_payload(payload)
    update_library_item(script_id, payload, db_path)

