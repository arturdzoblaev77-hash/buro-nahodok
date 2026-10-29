"""Клиент Bot API MAX (https://dev.max.ru/docs-api).

Домен platform-api2.max.ru, авторизация заголовком `Authorization: <token>`
(токен в query-параметрах больше не поддерживается).

Основные методы, используемые здесь:
  GET  /updates?marker=&timeout=&types=   — Long Polling
  POST /messages?chat_id=|user_id=        — отправка сообщения
  POST /answers?callback_id=              — ответ на нажатие кнопки (edit)
  POST /uploads?type=image -> {url,token} — загрузка медиафайла
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import ssl
import logging
from typing import Any, Optional

import httpx

from .config import settings

log = logging.getLogger("max")


class MaxApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"MAX API [{status}] {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


def _ssl_context() -> ssl.SSLContext:
    if settings.tls_no_verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    # Системное хранилище доверенных корней (Dockerfile ставит корни Минцифры).
    return ssl.create_default_context()


class MaxApi:
    def __init__(self) -> None:
        self._ssl = _ssl_context()
        self._client = httpx.AsyncClient(
            base_url=settings.max_api_url.rstrip("/"),
            headers={"Authorization": settings.bot_token},
            verify=self._ssl,
            timeout=httpx.Timeout(60.0, read=settings.poll_timeout + 15.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        resp = await self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            code = ""
            message = resp.text[:200]
            with contextlib.suppress(ValueError, json.JSONDecodeError):
                body = resp.json()
                code = body.get("code", "")
                message = body.get("message", message)
            raise MaxApiError(resp.status_code, str(code), message)
        return resp

    # ------------------------------ info --------------------------------- #

    async def get_me(self) -> dict[str, Any]:
        return (await self._request("GET", "/me")).json()

    # --------------------------- long polling ----------------------------- #

    async def get_updates(self, marker: Optional[int], timeout: Optional[int] = None,
                          types: str = "message_created,message_callback,bot_started") -> dict[str, Any]:
        params: dict[str, Any] = {"timeout": settings.poll_timeout if timeout is None else timeout,
                                  "types": types, "limit": 200}
        if marker is not None:
            params["marker"] = marker
        return (await self._request("GET", "/updates", params=params)).json()

    # ---------------------------- messages -------------------------------- #

    async def send_message(self, text: str, *, chat_id: Optional[int] = None,
                           user_id: Optional[int] = None,
                           keyboard: Optional[dict] = None,
                           photo_token: Optional[str] = None,
                           notify: bool = True) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if chat_id is not None:
            params["chat_id"] = chat_id
        elif user_id is not None:
            params["user_id"] = user_id
        else:
            raise ValueError("chat_id or user_id required")

        body: dict[str, Any] = {"text": text, "notify": notify, "format": "markdown"}
        attachments: list[dict[str, Any]] = []
        if photo_token:
            attachments.append({"type": "image", "payload": {"token": photo_token}})
        if keyboard:
            attachments.append(keyboard)
        if attachments:
            body["attachments"] = attachments
        return (await self._request("POST", "/messages", params=params, json=body)).json()

    async def send_message_retry(self, *args: Any, photo_token: Optional[str] = None,
                                 extra_wait: bool = False, **kwargs: Any) -> dict[str, Any]:
        """Отправка с учётом rate-limit (2 msg/s на диалог) и прогрева вложений."""
        delays = [0.05, 1.2, 3.0, 6.0]
        last_exc: Optional[MaxApiError] = None
        for i, delay in enumerate(delays):
            await asyncio.sleep(delay if i else 0.0)
            try:
                return await self.send_message(*args, photo_token=photo_token, **kwargs)
            except MaxApiError as exc:
                if extra_wait:
                    await asyncio.sleep(0.55)  # не более 2 сообщений/с в один чат
                    extra_wait = False
                if exc.code in ("attachment.not.ready", "attachment.file.not.processed") or "not.processed" in exc.message:
                    last_exc = exc
                    continue
                if exc.status == 429:
                    last_exc = exc
                    continue
                raise
        raise last_exc if last_exc else MaxApiError(500, "unknown", "send failed")

    # ----------------------------- answers -------------------------------- #

    async def answer_callback(self, callback_id: str, *, text: Optional[str] = None,
                              keyboard: Optional[dict] = None,
                              photo_token: Optional[str] = None) -> bool:
        """Отредактировать сообщение, из которого нажали кнопку (POST /answers)."""
        body: dict[str, Any] = {}
        if text is not None or keyboard or photo_token:
            message: dict[str, Any] = {"format": "markdown"}
            attachments: list[dict[str, Any]] = []
            if text:
                message["text"] = text
            if photo_token:
                attachments.append({"type": "image", "payload": {"token": photo_token}})
            if keyboard:
                attachments.append(keyboard)
            if attachments:
                message["attachments"] = attachments
            body["message"] = message
        try:
            resp = await self._request("POST", "/answers", params={"callback_id": callback_id}, json=body)
        except MaxApiError as exc:
            log.warning("answer_callback %s failed: %s", callback_id, exc)
            return False
        with contextlib.suppress(ValueError, json.JSONDecodeError):
            return bool(resp.json().get("success", True))
        return True

    # ------------------------------ uploads ------------------------------- #

    async def upload_image(self, data: bytes, filename: str = "photo.jpg") -> Optional[str]:
        """POST /uploads?type=image -> POST на полученный URL -> токен вложения."""
        try:
            r = (await self._request("POST", "/uploads", params={"type": "image"})).json()
            upload_url = r.get("url")
            token = r.get("token")
            if not upload_url or not token:
                log.warning("uploads: пустой ответ: %s", r)
                return None
            ok = False
            if upload_url.startswith("http"):
                # внутреннему upload-URL токен не нужен — отдельный клиент
                async with httpx.AsyncClient(verify=self._ssl, timeout=60.0) as upl:
                    uresp = await upl.post(upload_url, files={"data": (filename, data, "image/jpeg")})
                ok = uresp.status_code < 400
            if not ok:
                log.warning("uploads: файл не принят (status=%s)", ok)
                return None
            # файл обрабатывается асинхронно — даём немного времени
            await asyncio.sleep(1.5)
            return str(token)
        except (httpx.HTTPError, MaxApiError, ValueError) as exc:
            log.warning("upload_image failed: %s", exc)
            return None
