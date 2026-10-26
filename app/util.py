"""Общие утилиты форматирования."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .config import settings


def fmt_ts(ts: float | None) -> str:
    if not ts:
        return "—"
    dt = datetime.fromtimestamp(ts, tz=ZoneInfo(settings.timezone))
    return dt.strftime("%d.%m.%Y %H:%M")


def fmt_ts_short(ts: float | None) -> str:
    if not ts:
        return "—"
    dt = datetime.fromtimestamp(ts, tz=ZoneInfo(settings.timezone))
    return dt.strftime("%d.%m %H:%M")


def esc(text: str) -> str:
    """Экранирование для markdown-текста сообщений."""
    return (text or "").replace("[", "\\[").replace("]", "\\]")
