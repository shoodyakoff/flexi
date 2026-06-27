from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "scripts.sqlite"
CONTENT_INBOX_DIR = DATA_DIR / "inbox"
CONTENT_OUTBOX_DIR = DATA_DIR / "outbox"
KNOWLEDGE_DIR = ROOT / "knowledge"
REELS_INSTRUCTION_PATH = KNOWLEDGE_DIR / "reels_killer_instruction.md"
JOB_SEARCH_LIBRARY_PATH = KNOWLEDGE_DIR / "job_search_advice_library.md"
