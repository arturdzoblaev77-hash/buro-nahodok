"""Чат-бот «Бюро находок» — обработчики событий Bot API MAX.

Сценарий по ТЗ (слайды 7–11):
  7. «Старт» → приветствие и меню («Потеряно», «Найдено»)
  8. Кнопка «Потеряно» → список объявлений о найденных вещах (ждут владельца)
  9. Кнопка «Найденные вещи» → архив возвращённых вещей
 10. Кнопка «Нашёл вещь» → форма: категория → место → фото → объявление в БД
 11. Владелец находит вещь и отмечает её полученной (кнопка «Забрал»)
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import Any, Optional

from . import db
from .config import settings
from .keyboards import (CATEGORIES, btn_callback, category_keyboard,
                        kb, list_page_keyboard, main_menu, photo_step_keyboard)
from .max_api import MaxApi
from .util import esc, fmt_ts, fmt_ts_short
from .web_images import save_web_photo  # noqa: F401  (используется в web.py)

log = logging.getLogger("bot")

WELCOME = (
    "🎒 *Бюро находок* — все потеряшки школы в одном месте!\n\n"
    "Добро пожаловать! Здесь можно:\n"
    "🔎 посмотреть, какие вещи уже *найдены* и ждут своих владельцев;\n"
    "📦 *добавить объявление* о найденной вещи;\n"
    "📚 узнать, что везут чаще всего — *архив возвращённых* вещей.\n\n"
    "Выбери действие кнопкой ниже ⬇️"
)

GREETINGS = {"старт", "start", "/start", "начать", "меню"}

FORM_PLACE_HINT = (
    "📍 *Шаг 2 из 3.* Укажи *место*, где лежит вещь "
    "(например: «кабинет 301», «гардероб, 2-й ряд», «спортзал»)…"
)
FORM_PHOTO_HINT = (
    "📷 *Шаг 3 из 3.* Пришли *фото* вещи — так владельцу будет проще узнать её. "
    "Если фото нет, нажми «Без фото»…"
)


def _fmt_item(item: Any) -> str:
    lines = [
        f"№{item['id']} · *{esc(item['category'])}*",
        f"📍 Место: {esc(item['place'])}",
        f"🗓 Найдено: {fmt_ts_short(item['created_at'])}",
    ]
    if item["description"]:
        lines.append(f"💬 {esc(item['description'])[:200]}")
    if item["status"] == "found":
        lines.append("⏳ Ждёт владельца")
    else:
        by = item["returned_by_name"] or "владелец"
        lines.append(f"✅ Возвращена ({esc(by)}, {fmt_ts_short(item['returned_at'])})")
    return "\n".join(lines)


def _item_keyboard(item_id: int, returned: bool, web_url: str = "") -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = []
    if not returned:
        rows.append([btn_callback("✅ Это моя вещь — забрал!", "claim", id=item_id)])
    if web_url:
        rows.append([_link_card(web_url, item_id)])
    return kb(rows)


def _link_card(web_url: str, item_id: int) -> dict[str, Any]:
    return {"type": "link", "text": "🖥 Открыть в мини-приложении", "url": f"{web_url}/item/{item_id}"}


class Bot:
    def __init__(self, api: MaxApi) -> None:
        self.api = api
        self.web_url = settings.web_url
        self.page_size = settings.list_page_size
        # защита от гонки правки одного сообщения
        self._edit_lock = asyncio.Lock()

    # ---------------------------- dispatch -------------------------------- #

    async def handle(self, update: dict[str, Any]) -> None:
        utype = update.get("update_type")
        try:
            if utype == "bot_started":
                await self._on_bot_started(update)
            elif utype == "message_created":
                await self._on_message(update.get("message") or {})
            elif utype == "message_callback":
                await self._on_callback(update)
            else:
                return
        except Exception:
            log.exception("handler failed for update %s", utype)

    # ------------------------------- chat --------------------------------- #

    @staticmethod
    def _recipient(message: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
        rec = message.get("recipient") or {}
        sender = message.get("sender") or {}
        return rec.get("chat_id"), sender.get("user_id")

    async def send(self, text: str, *, chat_id: Optional[int], user_id: Optional[int],
                   keyboard: Optional[dict] = None, photo_token: Optional[str] = None) -> None:
        if chat_id:
            await self.api.send_message_retry(text, chat_id=chat_id, keyboard=keyboard,
                                              photo_token=photo_token, extra_wait=True)
        elif user_id:
            await self.api.send_message_retry(text, user_id=user_id, keyboard=keyboard,
                                              photo_token=photo_token, extra_wait=True)

    async def _on_bot_started(self, update: dict[str, Any]) -> None:
        user = update.get("user") or {}
        session = db.get_session(user.get("user_id", 0))
        if session:
            db.drop_session(user.get("user_id", 0))
        await self.send(WELCOME, chat_id=update.get("chat_id"), user_id=user.get("user_id"),
                        keyboard=main_menu(self.web_url))

    # ------------------------- message handlers ---------------------------- #

    async def _on_message(self, message: dict[str, Any]) -> None:
        body = message.get("body") or {}
        text = (body.get("text") or "").strip()
        sender = message.get("sender") or {}
        user_id = sender.get("user_id", 0)
        user_name = ((sender.get("first_name") or "") + " " + (sender.get("last_name") or "")).strip() or "Неизвестный"
        chat_id, uid = self._recipient(message)

        session = db.get_session(user_id)

        # 1) фото в форме
        photos = [a for a in (body.get("attachments") or []) if a.get("type") == "image"]
        if photos and session and session.get("step") == "photo":
            await self._form_photo(chat_id, uid, session, photos[0])
            return
        if photos and not session:
            await self.send("📷 Фотографии принимаю через форму: нажми ✅ «Нашёл вещь — добавить объявление» "
                            "в меню, чтобы создать объявление о находке.",
                            chat_id=chat_id, user_id=uid, keyboard=main_menu(self.web_url))
            return

        if not text:
            return
        lowered = text.lower().lstrip("/")

        # 2) команды/приветствие
        if lowered in GREETINGS:
            db.drop_session(user_id)
            await self.send(WELCOME, chat_id=chat_id, user_id=uid, keyboard=main_menu(self.web_url))
            return

        # 3) шаг формы «место»
        if session and session.get("step") == "place":
            session["place"] = text[:300]
            session["step"] = "photo"
            db.set_session(user_id, session)
            await self.send(FORM_PHOTO_HINT, chat_id=chat_id, user_id=uid,
                            keyboard=photo_step_keyboard())
            return

        # 4) неизвестный текст
        await self.send("Не понял тебя 🤔 Нажми кнопку ниже или отправь «Старт».",
                        chat_id=chat_id, user_id=uid, keyboard=main_menu(self.web_url))

    # --------------------------- форма добавления --------------------------- #

    async def _start_form(self, chat_id: Optional[int], user_id: Optional[int],
                          user_name: str = "Школьник") -> None:
        if user_id is None:
            return
        db.set_session(user_id, {"step": "category", "user_name": user_name[:120]})
        text = ("📦 *Добавление найденной вещи*\n\n"
                "🏷 *Шаг 1 из 3.* Выбери *категорию* вещи…")
        await self.send(text, chat_id=chat_id, user_id=user_id, keyboard=category_keyboard(CATEGORIES))

    async def _form_category(self, chat_id: Optional[int], user_id: Optional[int],
                             session: dict[str, Any], category: str) -> None:
        session.update(step="place", category=category)
        db.set_session(user_id, session)  # type: ignore[arg-type]
        await self.send(FORM_PLACE_HINT, chat_id=chat_id, user_id=user_id)

    async def _form_photo(self, chat_id: Optional[int], user_id: Optional[int],
                          session: dict[str, Any], photo: dict[str, Any]) -> None:
        payload = photo.get("payload") or {}
        photo_file, caption = "", ""
        url = payload.get("url")
        if url:
            photo_file, data = await self._download_photo(url)
            if data and session.get("category"):
                # пере-загружаем фото под токен бота, чтобы можно было
                # показывать вещь и в чате, и в мини-приложении
                token = await self.api.upload_image(data)
                if token:
                    session["photo_token"] = token
        if photo_file:
            session["photo_file"] = photo_file
        await self._publish(chat_id, user_id, session)
        if caption:
            await self.send(caption, chat_id=chat_id, user_id=user_id)

    async def _download_photo(self, url: str) -> tuple[str, bytes]:
        import httpx
        name = secrets.token_hex(8)
        ext = "jpg"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)
                data = resp.content
                ctype = (resp.headers.get("content-type") or "").lower()
                if "png" in ctype:
                    ext = "png"
                if not data or len(data) > 12 * 1024 * 1024:
                    return "", b""
        except httpx.HTTPError as exc:
            log.warning("photo download failed: %s", exc)
            return "", b""
        fname = f"{name}.{ext}"
        path = settings.photos_dir / fname
        settings.ensure_dirs()
        path.write_bytes(data)
        return fname, data

    async def _publish(self, chat_id: Optional[int], user_id: Optional[int],
                       session: dict[str, Any]) -> None:
        user_name = session.get("user_name") or "Школьник"
        item_id = db.add_item(
            category=session.get("category", "Другое"),
            place=session.get("place", "Не указано"),
            description=session.get("description", ""),
            photo_file=session.get("photo_file", ""),
            photo_token=session.get("photo_token", ""),
            user_id=user_id or 0,
            user_name=user_name,
        )
        db.drop_session(user_id)  # type: ignore[arg-type]
        item = db.get_item(item_id)
        text = ("✅ *Объявление №{} создано!*\n\n{}\n\n"
                "Владелец увидит его в разделе «Потеряно» и в мини-приложении. "
                "Когда вещь заберут — нажми «Забрал» прямо на этом сообщении.").format(
            item_id, _fmt_item(item))
        kb2 = _item_keyboard(item_id, False, self.web_url)
        token = session.get("photo_token") or None
        await self.send(text, chat_id=chat_id, user_id=user_id, keyboard=kb2, photo_token=token)

    # ---------------------------- списки/меню ------------------------------- #

    async def show_list(self, kind: str, page: int, chat_id: Optional[int],
                        user_id: Optional[int], callback_id: Optional[str] = None) -> None:
        status = "found" if kind == "lost" else "returned"
        items = db.list_items(status, limit=self.page_size + 1, offset=page * self.page_size)
        has_more = len(items) > self.page_size
        items = items[: self.page_size]
        if not items and page == 0:
            body = ("📭 Пока тут пусто.\n\n" +
                    ("Ничего не найдено — как только кто-то добавит объявление о найденной вещи, "
                     "оно появится здесь." if status == "found" else
                     "Возвращённых вещей ещё нет. Все объявлененные вещи ждут владельцев в разделе «Потеряно».")
                    )
            await self.send(body, chat_id=chat_id, user_id=user_id,
                            keyboard=list_page_keyboard(kind, 0, False, self.web_url))
            return

        lines = []
        for it in items:
            lines.append("—" * 22)
            lines.append(_fmt_item(it))
        head = ("🔎 *Найденные в школе вещи (ждут владельца)*"
                if status == "found" else "📚 *Архив: вещи вернулись владельцам*")
        footer = "\n\nНажми «Забрал» под объявлением, если вещь твоя ✅" if status == "found" else ""
        text = f"{head}\n\n" + "\n".join(lines) + footer
        page_kb = list_page_keyboard(kind, page, has_more, self.web_url)
        if callback_id:
            await self.api.answer_callback(callback_id, text=text, keyboard=page_kb)
        else:
            await self.send(text, chat_id=chat_id, user_id=user_id, keyboard=page_kb)

    async def _on_callback(self, update: dict[str, Any]) -> None:
        callback = update.get("callback") or {}
        raw = callback.get("payload") or "{}"
        callback_id = callback.get("callback_id")
        user = callback.get("user") or {}
        user_id = user.get("user_id", 0)
        user_name = ((user.get("first_name") or "") + " " + (user.get("last_name") or "")).strip() or "Неизвестный"
        chat_id, uid = self._recipient(callback.get("message") or {})
        if not chat_id and user_id:
            chat_id = None
        try:
            data = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            data = {}
        action = data.get("a", "")

        if action == "menu":
            db.drop_session(user_id)
            if callback_id:
                await self.api.answer_callback(callback_id, text=WELCOME, keyboard=main_menu(self.web_url))
            else:
                await self.show_menu(chat_id, user_id)
        elif action == "lost":
            await self.show_list("lost", int(data.get("p", 0)), chat_id, uid, callback_id)
        elif action == "archive":
            await self.show_list("archive", int(data.get("p", 0)), chat_id, uid, callback_id)
        elif action == "list":
            kind = str(data.get("k", "lost"))
            await self.show_list(kind, int(data.get("p", 0)), chat_id, uid, callback_id)
        elif action == "found":
            await self._start_form(chat_id, uid, user_name)
            if callback_id:
                await self.api.answer_callback(callback_id)  # просто вырубаем «часики»
        elif action == "cat":
            session = db.get_session(user_id) or {"step": "category"}
            await self._form_category(chat_id, uid, session, str(data.get("v", "Другое")))
            if callback_id:
                await self.api.answer_callback(callback_id)
        elif action == "skip_photo":
            session = db.get_session(user_id) or {}
            await self._publish(chat_id, uid, session)
            if callback_id:
                await self.api.answer_callback(callback_id)
        elif action == "cancel":
            db.drop_session(user_id)
            if callback_id:
                await self.api.answer_callback(
                    callback_id,
                    text="❌ Отменено. Что дальше?",
                    keyboard=main_menu(self.web_url),
                )
            else:
                await self.show_menu(chat_id, uid)
        elif action == "claim":
            await self._claim(chat_id, uid, user_name, int(data.get("id", 0)), callback_id)
        else:
            if callback_id:
                await self.api.answer_callback(callback_id)

    async def show_menu(self, chat_id: Optional[int], user_id: Optional[int]) -> None:
        await self.send(WELCOME, chat_id=chat_id, user_id=user_id, keyboard=main_menu(self.web_url))

    # ------------------------------ claim ---------------------------------- #

    async def _claim(self, chat_id: Optional[int], uid: int, uname: str,
                     item_id: int, callback_id: Optional[str]) -> None:
        result = db.claim_item(item_id, uid, uname)
        if result is None:
            if callback_id:
                await self.api.answer_callback(callback_id, text="Объявление уже не существует…")
            return
        item = result["item"]
        kb2 = _item_keyboard(item_id, True, self.web_url)
        text = "🎉 *Вещь вернулась владельцу!*\n\n" + _fmt_item(item)
        if callback_id:
            await self.api.answer_callback(callback_id, text=text, keyboard=kb2)
        else:
            await self.send(text, chat_id=chat_id, user_id=uid)
        st = db.stats()
        log.info("claim #%s: returned_by=%s, total=%s, returned=%s",
                 item_id, uname, st["total"], st["returned"])
