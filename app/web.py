"""Мини-приложение «Бюро находок» (страницы, которые открываются из MAX).

Слайд 8  → GET /lost     — объявления о найденных вещах (ждут владельца)
Слайд 9  → GET /archive  — архив: что уже вернули владельцам
Слайд 10 → GET /found    — форма добавления: место + фото + категория
Слайд 11 → POST /claim   — владелец отметила вещь полученной
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import db
from .config import settings
from .keyboards import CATEGORIES
from .util import fmt_ts, fmt_ts_short
from .web_images import save_web_photo

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _deftime(value):
    return fmt_ts(value) if value else "—"


def _defmy(value):
    return fmt_ts_short(value) if value else "—"


templates.env.filters["deftime"] = _deftime
templates.env.filters["defmy"] = _defmy

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    st = db.stats()
    return templates.TemplateResponse("index.html", {
        "request": request, "st": st, "categories": CATEGORIES,
    })


@router.get("/healthz")
async def healthz():
    return {"ok": True, "items": db.stats()}


@router.get("/lost", response_class=HTMLResponse)
async def lost(request: Request, offset: int = 0):
    items = db.list_items("found", limit=50, offset=max(offset, 0))
    st = db.stats()
    return templates.TemplateResponse("list.html", {
        "request": request,
        "items": items,
        "status": "found",
        "title": "🔎 Найденные вещи — ждут владельца",
        "subtitle": "Все вещи, найденные в школе. Узнал свою — забирай и отметь здесь ⬇️",
        "empty": "Пока ничего не найдено. Загляни позже!",
        "st": st,
        "page": max(offset, 0) // 50,
    })


@router.get("/archive", response_class=HTMLResponse)
async def archive(request: Request, offset: int = 0):
    items = db.list_items("returned", limit=50, offset=max(offset, 0))
    st = db.stats()
    return templates.TemplateResponse("list.html", {
        "request": request,
        "items": items,
        "status": "returned",
        "title": "📚 Найденные вещи: архив возвратов",
        "subtitle": "Эти вещи уже вернулись владельцам. Так школа видит, что теряется чаще всего.",
        "empty": "Архив пока пуст — как только владелец отметит вещь «Забрал», она появится здесь.",
        "st": st,
        "page": max(offset, 0) // 50,
    })


@router.get("/found", response_class=HTMLResponse)
async def found_form(request: Request):
    return templates.TemplateResponse("form.html", {
        "request": request, "categories": CATEGORIES, "error": None,
    })


@router.post("/found", response_class=HTMLResponse)
async def found_submit(
    request: Request,
    category: str = Form("Другое"),
    place: str = Form(""),
    description: str = Form(""),
    who: str = Form(""),
    photo: Optional[UploadFile] = File(None),
):
    error: Optional[str] = None
    if not place.strip():
        error = "Укажи, где лежит вещь 📍"
    elif photo is not None and photo.size and photo.size > 10 * 1024 * 1024:
        error = "Фото больше 10 МБ — сожми или пришли без него 📷"
    elif photo is not None and (photo.content_type or "").split(";")[0].split("/")[0] != "image":
        error = "Это не похоже на фотографию 🤔"

    if error:
        return templates.TemplateResponse("form.html", {
            "request": request, "categories": CATEGORIES, "error": error,
        }, status_code=400)

    filename = ""
    if photo is not None and photo.filename:
        data = await photo.read()
        if data:
            filename = save_web_photo(data, photo.content_type or "")
    item_id = db.add_item(
        category=category.strip()[:60] or "Другое",
        place=place.strip()[:300],
        description=description.strip()[:500],
        photo_file=filename,
        user_name=who.strip()[:120] or "Друг",
    )
    return templates.TemplateResponse("created.html", {
        "request": request, "item_id": item_id,
    })


@router.get("/item/{item_id}", response_class=HTMLResponse)
async def item_page(request: Request, item_id: int):
    it = db.get_item(item_id)
    if it is None:
        raise HTTPException(404, detail="Объявление не найдено")
    return templates.TemplateResponse("item.html", {
        "request": request, "item": it,
    })


@router.post("/claim/{item_id}")
async def claim_web(request: Request, item_id: int, who: str = Form("")):
    it = db.get_item(item_id)
    if it is None:
        raise HTTPException(404, detail="Объявление не найдено")
    if it["status"] != "found":
        return RedirectResponse(f"/item/{item_id}", status_code=303)
    name = who.strip()[:120] or "Владелец"
    db.claim_item(item_id, 0, name)
    return RedirectResponse(f"/item/{item_id}", status_code=303)


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    st = db.stats()
    return templates.TemplateResponse("stats.html", {
        "request": request, "st": st,
    })
