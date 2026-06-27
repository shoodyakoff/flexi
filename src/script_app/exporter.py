from __future__ import annotations

import json
import re
from pathlib import Path

from src.cli import _annotate_voiceover_text
from src.schemas import ProductInsert, VideoScript

from .db import get_script, update_script
from .paths import DEFAULT_DB_PATH, ROOT


def slugify(text: str) -> str:
    normalized = text.lower().strip()
    normalized = re.sub(r"[^\w\s-]", "", normalized, flags=re.UNICODE)
    normalized = re.sub(r"[\s_-]+", "-", normalized)
    return normalized.strip("-")[:70] or "script"


def export_slug(row: dict[str, object]) -> str:
    title_slug = slugify(str(row["title"]))
    external_key = str(row.get("external_key") or "").strip()
    if str(row["title"]).startswith("РОЛИК №") and external_key:
        return f"{slugify(external_key)}-{title_slug}"
    if title_slug.startswith("20"):
        return title_slug
    from datetime import date

    return f"{date.today().isoformat()}-{title_slug}"


def annotate_voiceover(script_id: int, db_path: Path | str = DEFAULT_DB_PATH) -> str:
    row = get_script(script_id, db_path)
    if row["direction"] != "generated_video":
        raise ValueError("voiceover markup is only available for generated_video scripts")
    source = row["voiceover_text_raw"] or row["voiceover_text_marked"]
    marked = _annotate_voiceover_text(source)
    update_script(script_id, {"voiceover_text_marked": marked}, db_path)
    return marked


def export_video_script(
    script_id: int,
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    scripts_dir: Path | None = None,
) -> Path:
    row = get_script(script_id, db_path)
    if row["direction"] != "generated_video":
        raise ValueError("challenge scripts cannot be exported to the video pipeline")
    if not row["hook_asset_id"] or not row["cta_asset_id"]:
        raise ValueError("hook_asset_id and cta_asset_id are required before export")

    marked = row["voiceover_text_marked"] or annotate_voiceover(script_id, db_path)
    slug = export_slug(row)

    product_insert = None
    export_context = f"{row['hook_text']} {marked} {row['cta_text']}".lower()
    if "сопровод" in export_context:
        product_insert = ProductInsert(
            file="assets/broll_brand/broll_soprovod.mov",
            anchor_root="сопровод",
        )

    script = VideoScript(
        slug=slug,
        hook_id=row["hook_asset_id"],
        cta_id=row["cta_asset_id"],
        voiceover_text=marked,
        caption_text=row["caption_text"] or None,
        pinned_comment=row["pinned_comment"] or None,
        broll_strategy="auto",
        product_insert=product_insert,
    )
    out_dir = scripts_dir or ROOT / "scripts"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{slug}.json"
    out_path.write_text(
        script.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )
    update_script(
        script_id,
        {
            "status": "ready_to_shoot",
            "exported_script_path": _display_path(out_path),
            "voiceover_text_marked": marked,
        },
        db_path,
    )
    # Parse back through the schema and JSON loader to catch accidental invalid output.
    VideoScript(**json.loads(out_path.read_text(encoding="utf-8")))
    return out_path


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)
