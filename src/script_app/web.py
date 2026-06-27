from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.assets import list_assets
from src.cli import _annotate_voiceover_text

from .db import (
    SCRIPT_STATUS_OPTIONS,
    canonical_script_status,
    create_challenge_backlog_item,
    create_script,
    get_challenge_backlog_item,
    get_library_item,
    get_script,
    infer_backlog_due_date,
    init_db,
    legacy_direction_for_item_type,
    list_challenge_backlog,
    list_challenge_backlog_for_episode,
    list_scripts,
    normalize_item_type,
    shot_status_for_script_status,
    update_challenge_backlog_item,
    update_library_item,
    update_script,
)
from .exporter import annotate_voiceover, export_video_script
from .generator import generate_generated_video_script
from .paths import DEFAULT_DB_PATH

TEMPLATES_DIR = Path(__file__).with_name("templates")
STATIC_DIR = Path(__file__).with_name("static")

STATUS_LABELS = {
    "idea": "идея",
    "script": "сценарий",
    "ready_to_shoot": "готово к съемке",
    "shot": "снято",
    "published": "опубликовано",
    "draft": "сценарий",
    "ready": "готово к съемке",
    "exported": "готово к съемке",
    "scripted": "сценарий",
    "archived": "идея",
}
BACKLOG_STATUS_LABELS = {
    **STATUS_LABELS,
    "planned": "идея",
    "deferred": "идея",
}
ITEM_TYPE_LABELS = {
    "generated_video": "Видео под ключ",
    "challenge_episode": "Челлендж",
    "image_explainer": "Объяснять с картинками",
    "challenge_backlog": "Бэклог челенджа",
}
DIRECTION_LABELS = {
    **ITEM_TYPE_LABELS,
    "challenge_ai_income": ITEM_TYPE_LABELS["challenge_episode"],
    "explain_with_images": ITEM_TYPE_LABELS["image_explainer"],
}
FUNNEL_STAGE_LABELS = {
    "TOFU": "охват",
    "MOFU": "прогрев",
    "BOFU": "продажа",
}
CTA_TYPE_LABELS = {
    "save": "сохранить",
    "link": "ссылка",
    "keyword": "кодовое слово",
    "pinned": "закреп",
    "other": "другое",
}
CHALLENGE_DIRECTION_LABELS = {
    "diary": "дневник челенджа",
    "craft": "ремесло / вайбкодинг",
    "marketing_agent": "маркетинговые агенты",
    "personal_story": "личная история",
    "client_build": "заказная разработка",
}
HOOK_CLASS_LABELS = {
    "process": "процесс",
    "result": "результат",
    "failure": "провал",
    "self_reference": "сам ролик как доказательство",
    "live_reset": "лайв / перезагрузка",
}
BACKLOG_CATEGORY_OPTIONS = [
    "Личная история",
    "Тиндер для кроссовок",
    "Производство контента",
    "Месячные отчёты",
    "Отложенные возвращения",
    "Маркетинговые агенты",
]
BUSINESS_STREAM_OPTIONS = [
    "Сопровод",
    "SaaS",
    "обучение",
    "заказная разработка",
    "контент-завод",
    "Сопровод / обучение / заказная разработка",
    "SaaS / обучение / заказная разработка",
    "обучение / SaaS",
]
SOURCE_LABELS = {
    "manual": "вручную",
    "reels-backlog": "импорт из бэклога",
    "followup": "отложенное возвращение",
    "strategy": "стратегия",
    "dialogue": "из диалога",
}
MESSAGE_LABELS = {
    "saved": "Сохранено",
    "voiceover-prepared": "Озвучка подготовлена",
}
ERROR_LABELS = {
    "voiceover markup is only available for generated_video scripts": (
        "Разметка озвучки доступна только для роликов «Видео под ключ»."
    ),
    "challenge scripts cannot be exported to the video pipeline": (
        "Ролики челенджа пока нельзя экспортировать в пайплайн сборки."
    ),
    "hook_asset_id and cta_asset_id are required before export": (
        "Перед экспортом выбери видео-хук и видео-призыв."
    ),
}


def _ui_context() -> dict[str, Any]:
    return {
        "status_labels": STATUS_LABELS,
        "backlog_status_labels": BACKLOG_STATUS_LABELS,
        "item_type_labels": ITEM_TYPE_LABELS,
        "direction_labels": DIRECTION_LABELS,
        "funnel_stage_labels": FUNNEL_STAGE_LABELS,
        "cta_type_labels": CTA_TYPE_LABELS,
        "challenge_direction_labels": CHALLENGE_DIRECTION_LABELS,
        "hook_class_labels": HOOK_CLASS_LABELS,
        "backlog_category_options": BACKLOG_CATEGORY_OPTIONS,
        "business_stream_options": BUSINESS_STREAM_OPTIONS,
        "source_labels": SOURCE_LABELS,
    }


def _date_from_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _message_label(value: str) -> str:
    if not value:
        return ""
    if value.startswith("exported:"):
        return f"Экспортировано: {value.removeprefix('exported:')}"
    return MESSAGE_LABELS.get(value, value)


def _error_label(value: str) -> str:
    if not value:
        return ""
    return ERROR_LABELS.get(value, value)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db(DEFAULT_DB_PATH)
    yield


app = FastAPI(title="Библиотека сценариев", lifespan=lifespan)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _form_value(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _status_payload(status: str, current_status: str = "idea") -> dict[str, str]:
    canonical = canonical_script_status(status or current_status)
    return {
        "status": canonical,
        "shot_status": shot_status_for_script_status(canonical),
    }


def _asset_options(asset_type: str) -> list[str]:
    try:
        return sorted(list_assets(asset_type).keys())  # type: ignore[arg-type]
    except Exception:
        return []


def _generated_raw_text(
    *,
    hook: str,
    voiceover: str,
    cta: str,
    caption: str,
    pinned_comment: str,
) -> str:
    return (
        f"ХУК:\n{hook}\n\n"
        f"ОСНОВНОЙ ТЕКСТ:\n{voiceover}\n\n"
        f"ПРИЗЫВ:\n{cta}\n\n"
        f"ОПИСАНИЕ:\n{caption}\n\n"
        f"ЗАКРЕПЛЁННЫЙ КОММЕНТАРИЙ:\n{pinned_comment}"
    )


def _explainer_raw_text(
    *,
    hook: str,
    script: str,
    cta: str,
    storyboard: str,
) -> str:
    return (
        f"ХУК:\n{hook}\n\n"
        f"СЦЕНАРИЙ:\n{script}\n\n"
        f"ПРИЗЫВ:\n{cta}\n\n"
        f"КАРТИНКИ / ЭКРАННЫЕ ОПОРЫ:\n{storyboard}"
    )


def _item_neighbors(row: dict[str, Any]) -> tuple[Any | None, Any | None]:
    rows = (
        list_challenge_backlog(db_path=DEFAULT_DB_PATH)
        if row["item_type"] == "challenge_backlog"
        else list_scripts(item_type=row["item_type"], db_path=DEFAULT_DB_PATH)
    )
    ids = [item["id"] for item in rows]
    try:
        index = ids.index(row["id"])
    except ValueError:
        return None, None
    previous_row = rows[index - 1] if index > 0 else None
    next_row = rows[index + 1] if index + 1 < len(rows) else None
    return previous_row, next_row


def _backlog_categories() -> list[str]:
    known_categories = {
        row["category"]
        for row in list_challenge_backlog(db_path=DEFAULT_DB_PATH)
        if row["category"]
    }
    return sorted(known_categories | set(BACKLOG_CATEGORY_OPTIONS))


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    item_type: str = "",
    direction: str = "",
    status: str = "",
    q: str = "",
    funnel_stage: str = "",
    cta_type: str = "",
    category: str = "",
) -> HTMLResponse:
    active_type = normalize_item_type(item_type or direction or "generated_video")
    if active_type == "challenge_backlog":
        rows = list_challenge_backlog(
            status=status or None,
            category=category or None,
            query=q or None,
            db_path=DEFAULT_DB_PATH,
        )
    else:
        rows = list_scripts(
            item_type=active_type,
            status=status or None,
            query=q or None,
            funnel_stage=funnel_stage or None,
            cta_type=cta_type or None,
            db_path=DEFAULT_DB_PATH,
        )
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "rows": rows,
            "item_type": active_type,
            "direction": legacy_direction_for_item_type(active_type),
            "status": status,
            "q": q,
            "funnel_stage": funnel_stage,
            "cta_type": cta_type,
            "category": category,
            "categories": _backlog_categories(),
            "status_options": SCRIPT_STATUS_OPTIONS,
            "backlog_status_options": SCRIPT_STATUS_OPTIONS,
            "active_tab": active_type,
            "message": _message_label(request.query_params.get("message", "")),
            "error": _error_label(request.query_params.get("error", "")),
            **_ui_context(),
        },
    )


@app.get("/challenge-backlog", response_class=HTMLResponse)
def challenge_backlog(
    request: Request,
    status: str = "",
    category: str = "",
    q: str = "",
) -> HTMLResponse:
    return index(
        request,
        item_type="challenge_backlog",
        status=status,
        category=category,
        q=q,
    )


@app.get("/challenge-backlog/{item_id}", response_class=HTMLResponse)
def challenge_backlog_detail(request: Request, item_id: int) -> HTMLResponse:
    return detail(request, item_id)


@app.post("/challenge-backlog/create")
async def create_challenge_backlog(request: Request) -> RedirectResponse:
    form = dict(await request.form())
    source_episode_id_raw = _form_value(form, "source_episode_id")
    source_episode_id = int(source_episode_id_raw) if source_episode_id_raw.isdigit() else None
    due_date = _form_value(form, "due_date")
    if not due_date:
        base_date = date.today()
        if source_episode_id:
            try:
                source_episode = get_library_item(source_episode_id, DEFAULT_DB_PATH)
                base_date = _date_from_iso(source_episode["created_at"]) or base_date
            except KeyError:
                source_episode_id = None
        due_date = infer_backlog_due_date(_form_value(form, "return_window"), base_date) or ""
    item_id = create_challenge_backlog_item(
        {
            "title": _form_value(form, "title") or "Новая тема бэклога",
            "category": _form_value(form, "category"),
            "challenge_pillar": _form_value(form, "challenge_pillar")
            or _form_value(form, "pillar"),
            "business_stream": _form_value(form, "business_stream") or None,
            "status": _form_value(form, "status") or "idea",
            "source": "followup" if source_episode_id else "manual",
            "source_episode_id": source_episode_id,
            "hook_class": _form_value(form, "hook_class")
            or _form_value(form, "suggested_hook_class"),
            "return_window": _form_value(form, "return_window"),
            "due_date": due_date or None,
            "notes": _form_value(form, "notes"),
        },
        DEFAULT_DB_PATH,
    )
    if source_episode_id:
        return RedirectResponse(f"/scripts/{source_episode_id}?message=saved", status_code=303)
    return RedirectResponse(f"/scripts/{item_id}?message=saved", status_code=303)


@app.post("/challenge-backlog/{item_id}/status")
async def save_challenge_backlog_status(item_id: int, request: Request) -> RedirectResponse:
    return await save_status(item_id, request)


@app.post("/challenge-backlog/{item_id}")
async def save_challenge_backlog_item(item_id: int, request: Request) -> RedirectResponse:
    return await save(item_id, request)


@app.post("/generate")
async def generate(request: Request) -> RedirectResponse:
    form = dict(await request.form())
    stage = _form_value(form, "funnel_stage") or "TOFU"
    cta_type = _form_value(form, "cta_type") or "save"
    draft = generate_generated_video_script(
        topic=_form_value(form, "topic"),
        funnel_stage=stage,
        cta_type=cta_type,
        format_name=_form_value(form, "format_name") or "основной",
        context=_form_value(form, "context"),
    )
    script_id = create_script(
        {
            "item_type": "generated_video",
            "title": draft.title,
            "status": "script",
            "funnel_stage": stage,
            "cta_type": cta_type,
            "hook_text": draft.hook,
            "voiceover_text_raw": draft.voiceover,
            "voiceover_text_marked": "",
            "cta_text": draft.cta,
            "caption_text": draft.caption_text,
            "pinned_comment": draft.pinned_comment,
            "notes": draft.notes,
            "raw_text": _generated_raw_text(
                hook=draft.hook,
                voiceover=draft.voiceover,
                cta=draft.cta,
                caption=draft.caption_text,
                pinned_comment=draft.pinned_comment,
            ),
        },
        db_path=DEFAULT_DB_PATH,
    )
    return RedirectResponse(f"/scripts/{script_id}", status_code=303)


@app.post("/challenge/create")
async def create_challenge(request: Request) -> RedirectResponse:
    form = dict(await request.form())
    day = _form_value(form, "day_number")
    title = _form_value(form, "title")
    episode_label = _form_value(form, "episode_label")
    script_id = create_script(
        {
            "item_type": "challenge_episode",
            "title": title or f"{episode_label or 'Новый выпуск'} — челлендж",
            "status": _form_value(form, "status") or "script",
            "day_number": int(day) if day.isdigit() else None,
            "episode_label": episode_label,
            "core_topic": _form_value(form, "core_topic"),
            "business_stream": _form_value(form, "business_stream") or None,
            "challenge_pillar": _form_value(form, "challenge_pillar"),
            "hook_class": _form_value(form, "hook_class"),
            "opening_line": _form_value(form, "opening_line"),
            "script_text": _form_value(form, "script_text"),
            "cta_text": _form_value(form, "cta_text"),
            "shot_status": shot_status_for_script_status(_form_value(form, "status") or "script"),
        },
        db_path=DEFAULT_DB_PATH,
    )
    return RedirectResponse(f"/scripts/{script_id}", status_code=303)


@app.post("/explain-with-images/create")
async def create_explainer(request: Request) -> RedirectResponse:
    form = dict(await request.form())
    hook_text = _form_value(form, "hook_text")
    script_text = _form_value(form, "script_text")
    cta_text = _form_value(form, "cta_text")
    storyboard_text = _form_value(form, "storyboard_text")
    script_id = create_script(
        {
            "item_type": "image_explainer",
            "title": _form_value(form, "title") or "Новый рилс — объяснять с картинками",
            "status": "script",
            "core_topic": _form_value(form, "core_topic"),
            "series_key": _form_value(form, "series_key") or "trend-explainer",
            "hook_text": hook_text,
            "script_text": script_text,
            "cta_text": cta_text,
            "storyboard_text": storyboard_text,
            "format_notes": _form_value(form, "format_notes"),
            "raw_text": _explainer_raw_text(
                hook=hook_text,
                script=script_text,
                cta=cta_text,
                storyboard=storyboard_text,
            ),
        },
        db_path=DEFAULT_DB_PATH,
    )
    return RedirectResponse(f"/scripts/{script_id}", status_code=303)


@app.get("/scripts/{script_id}", response_class=HTMLResponse)
def detail(request: Request, script_id: int) -> HTMLResponse:
    try:
        row = get_library_item(script_id, DEFAULT_DB_PATH)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    previous_row, next_row = _item_neighbors(row)
    related_backlog = (
        list_challenge_backlog_for_episode(row["id"], DEFAULT_DB_PATH)
        if row["item_type"] == "challenge_episode"
        else []
    )
    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "request": request,
            "row": row,
            "item": row,
            "previous_row": previous_row,
            "next_row": next_row,
            "related_backlog": related_backlog,
            "hook_options": _asset_options("hooks"),
            "cta_options": _asset_options("ctas"),
            "error": _error_label(request.query_params.get("error", "")),
            "message": _message_label(request.query_params.get("message", "")),
            "status_options": SCRIPT_STATUS_OPTIONS,
            "backlog_status_options": SCRIPT_STATUS_OPTIONS,
            "active_tab": row["item_type"],
            **_ui_context(),
        },
    )


@app.post("/scripts/{script_id}/status")
async def save_status(script_id: int, request: Request) -> RedirectResponse:
    form = dict(await request.form())
    row = get_library_item(script_id, DEFAULT_DB_PATH)
    update_library_item(script_id, _status_payload(_form_value(form, "status"), row["status"]), DEFAULT_DB_PATH)
    next_url = _form_value(form, "next") or f"/?item_type={row['item_type']}"
    return RedirectResponse(next_url if next_url.startswith("/") else "/", status_code=303)


@app.post("/scripts/{script_id}")
async def save(script_id: int, request: Request) -> RedirectResponse:
    form = dict(await request.form())
    row = get_library_item(script_id, DEFAULT_DB_PATH)
    common: dict[str, Any] = {
        "title": _form_value(form, "title"),
        "status": canonical_script_status(_form_value(form, "status") or row["status"]),
        "notes": _form_value(form, "notes"),
    }
    if row["item_type"] == "generated_video":
        hook_text = _form_value(form, "hook_text")
        voiceover_text_raw = _form_value(form, "voiceover_text_raw")
        voiceover_text_marked = (
            _annotate_voiceover_text(voiceover_text_raw)
            if voiceover_text_raw
            else _form_value(form, "voiceover_text_marked")
        )
        cta_text = _form_value(form, "cta_text")
        caption_text = _form_value(form, "caption_text")
        pinned_comment = _form_value(form, "pinned_comment")
        common.update(
            {
                "funnel_stage": _form_value(form, "funnel_stage"),
                "cta_type": _form_value(form, "cta_type"),
                "keyword": _form_value(form, "keyword") or None,
                "hook_text": hook_text,
                "voiceover_text_raw": voiceover_text_raw,
                "voiceover_text_marked": voiceover_text_marked,
                "cta_text": cta_text,
                "caption_text": caption_text,
                "pinned_comment": pinned_comment,
                "hook_asset_id": _form_value(form, "hook_asset_id") or None,
                "cta_asset_id": _form_value(form, "cta_asset_id") or None,
                "raw_text": _generated_raw_text(
                    hook=hook_text,
                    voiceover=voiceover_text_raw,
                    cta=cta_text,
                    caption=caption_text,
                    pinned_comment=pinned_comment,
                ),
            }
        )
    elif row["item_type"] == "challenge_episode":
        day = _form_value(form, "day_number")
        common.update(
            {
                "day_number": int(day) if day.isdigit() else None,
                "episode_label": _form_value(form, "episode_label"),
                "opening_line": _form_value(form, "opening_line"),
                "script_text": _form_value(form, "script_text"),
                "core_topic": _form_value(form, "core_topic"),
                "business_stream": _form_value(form, "business_stream") or None,
                "challenge_pillar": _form_value(form, "challenge_pillar"),
                "hook_class": _form_value(form, "hook_class"),
                "series_key": _form_value(form, "series_key"),
                "storyboard_text": _form_value(form, "storyboard_text"),
                "decisions_text": _form_value(form, "decisions_text"),
                "replaced_draft_text": _form_value(form, "replaced_draft_text"),
                "deferred_results_text": _form_value(form, "deferred_results_text"),
                "format_notes": _form_value(form, "format_notes"),
                "cta_text": _form_value(form, "cta_text"),
                "shot_status": shot_status_for_script_status(common["status"]),
            }
        )
    elif row["item_type"] == "image_explainer":
        hook_text = _form_value(form, "hook_text")
        script_text = _form_value(form, "script_text")
        cta_text = _form_value(form, "cta_text")
        storyboard_text = _form_value(form, "storyboard_text")
        common.update(
            {
                "core_topic": _form_value(form, "core_topic"),
                "series_key": _form_value(form, "series_key"),
                "hook_text": hook_text,
                "script_text": script_text,
                "cta_text": cta_text,
                "storyboard_text": storyboard_text,
                "decisions_text": _form_value(form, "decisions_text"),
                "format_notes": _form_value(form, "format_notes"),
                "shot_status": shot_status_for_script_status(common["status"]),
                "raw_text": _explainer_raw_text(
                    hook=hook_text,
                    script=script_text,
                    cta=cta_text,
                    storyboard=storyboard_text,
                ),
            }
        )
    else:
        due_date = _form_value(form, "due_date")
        if not due_date:
            base_date = _date_from_iso(row["source_created_at"]) or date.today()
            due_date = infer_backlog_due_date(_form_value(form, "return_window"), base_date) or ""
        common.update(
            {
                "category": _form_value(form, "category"),
                "challenge_pillar": _form_value(form, "challenge_pillar")
                or _form_value(form, "pillar"),
                "business_stream": _form_value(form, "business_stream") or None,
                "source": _form_value(form, "source") or "manual",
                "hook_class": _form_value(form, "hook_class")
                or _form_value(form, "suggested_hook_class"),
                "return_window": _form_value(form, "return_window"),
                "due_date": due_date or None,
            }
        )
    update_library_item(script_id, common, DEFAULT_DB_PATH)
    next_url = _form_value(form, "next")
    if next_url.startswith("/"):
        return RedirectResponse(next_url, status_code=303)
    return RedirectResponse(f"/scripts/{script_id}?message=saved", status_code=303)


@app.post("/scripts/{script_id}/annotate")
def annotate(script_id: int) -> RedirectResponse:
    try:
        annotate_voiceover(script_id, DEFAULT_DB_PATH)
    except Exception as exc:
        return RedirectResponse(f"/scripts/{script_id}?error={exc}", status_code=303)
    return RedirectResponse(f"/scripts/{script_id}?message=voiceover-prepared", status_code=303)


@app.post("/scripts/{script_id}/export")
def export(script_id: int) -> RedirectResponse:
    try:
        path = export_video_script(script_id, DEFAULT_DB_PATH)
    except Exception as exc:
        return RedirectResponse(f"/scripts/{script_id}?error={exc}", status_code=303)
    return RedirectResponse(f"/scripts/{script_id}?message=exported:{path}", status_code=303)

