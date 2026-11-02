"""Точка входа: FastAPI-приложение (мини-приложение + long polling бот)."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import db
from .bot import Bot
from .config import settings
from .max_api import MaxApi
from .web import router as web_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("main")


async def poll_loop(api: MaxApi, bot: Bot) -> None:
    """Long Polling: GET /updates → обработка обновлений → сдвиг marker."""
    marker: Optional[int] = None
    backoff = 1.0
    while True:
        try:
            data = await api.get_updates(marker)
            updates = data.get("updates") or []
            new_marker = data.get("marker")
            if new_marker is not None:
                marker = int(new_marker)
            for upd in updates:
                await bot.handle(upd)
            backoff = 1.0
            if not updates:
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # сеть/токен — цикл ронять нельзя
            log.warning("poll error: %s (ждём %.0f с)", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


def create_app() -> FastAPI:
    settings.ensure_dirs()
    db.init_db()

    api = MaxApi() if settings.bot_token else None
    task: Optional[asyncio.Task] = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal task
        # startup
        if api is None:
            log.warning(
                "BOT_TOKEN не задан — запущено только мини-приложение: %s:%s",
                settings.host, settings.port,
            )
        else:
            try:
                me = await api.get_me()
                log.info("Бот MAX на связи: @%s (%s)",
                         me.get("username", "?"), me.get("name", me.get("first_name", "?")))
            except Exception as exc:
                log.error("MAX API недоступен: %s — продолжаю попытки в цикле опроса", exc)
            task = asyncio.create_task(poll_loop(api, Bot(api)))
        yield
        # shutdown
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if api is not None:
            await api.close()

    app = FastAPI(
        title="Бюро находок",
        version="1.0",
        description="Чат-бот «Бюро находок» для школы — платформа MAX. Трек «Школа — это больше, чем уроки».",
        docs_url="/docs",
        lifespan=lifespan,
    )

    app.include_router(web_router)
    app.mount("/photos", StaticFiles(directory=str(settings.photos_dir), check_dir=False),
              name="photos")
    app.mount("/static", StaticFiles(directory="static"), name="static")

    return app


app = create_app()
