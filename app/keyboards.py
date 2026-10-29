"""Клавиатуры (inline_keyboard) и кнопки для Bot API MAX."""
from __future__ import annotations

import json
from typing import Any, Optional


def btn_callback(text: str, action: str, **facts: Any) -> dict[str, Any]:
    payload = {"a": action}
    payload.update(facts)
    return {
        "type": "callback",
        "text": text[:64],
        "payload": json.dumps(payload, ensure_ascii=False)[:1024],
    }


def btn_link(text: str, url: str) -> dict[str, Any]:
    return {"type": "link", "text": text[:64], "url": url}


def kb(rows: list[list[dict[str, Any]]]) -> dict[str, Any]:
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


CATEGORIES: list[str] = [
    "Обувь",
    "Одежда",
    "Гаджеты и техника",
    "Учебные вещи",
    "Спортинвентарь",
    "Другое",
]


def category_keyboard(cats: list[str]) -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = []
    pair: list[dict[str, Any]] = []
    for cat in cats:
        pair.append(btn_callback(cat, "cat", v=cat))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([btn_callback("✖️ Отмена", "cancel")])
    return kb(rows)


def photo_step_keyboard() -> dict[str, Any]:
    return kb([
        [btn_callback("🚫 Без фото", "skip_photo")],
        [btn_callback("✖️ Отмена", "cancel")],
    ])


def main_menu(web_url: str = "") -> dict[str, Any]:
    rows = [
        [btn_callback("🔎 Потеряно", "lost")],
        [btn_callback("📦 Нашёл вещь — добавить объявление", "found")],
        [btn_callback("📚 Найденные вещи (архив)", "archive")],
    ]
    if web_url:
        rows.append([btn_link("🖥 Открыть мини-приложение", web_url + "/")])
    return kb(rows)


def _page_path(kind: str, page: int) -> str:
    base = {"lost": "/lost", "archive": "/archive"}.get(kind, "/")
    if kind in ("lost", "archive") and page > 0:
        return f"{base}?offset={page * 50}"
    return base


def list_page_keyboard(kind: str, page: int, has_more: bool, web_url: str = "") -> dict[str, Any]:
    row: list[dict[str, Any]] = []
    if page > 0:
        row.append(btn_callback("◀️ Назад", "list", k=kind, p=page - 1))
    if has_more:
        row.append(btn_callback("Вперёд ▶️", "list", k=kind, p=page + 1))
    rows: list[list[dict[str, Any]]] = [row] if row else []
    if web_url:
        host = web_url.split("//")[-1]
        rows.append([btn_link(f"🖥 Открыть полную версию ({host})", web_url + _page_path(kind, page))])
    rows.append([btn_callback("🏠 В меню", "menu")])
    return kb(rows)
