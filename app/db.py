"""SQLite-слой данных «Бюро находок»."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from .config import settings

# status='found'    — вещь найдена, объявление ждёт владельца (слайд 8)
# status='returned' — вещь возвращена владельцу (архив, слайд 9)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    status           TEXT NOT NULL DEFAULT 'found',
    category         TEXT NOT NULL,
    place            TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    photo_file       TEXT NOT NULL DEFAULT '',
    photo_token      TEXT NOT NULL DEFAULT '',
    created_by_id    INTEGER NOT NULL DEFAULT 0,
    created_by_name  TEXT NOT NULL DEFAULT '',
    created_at       REAL NOT NULL,
    returned_at      REAL,
    returned_by_id   INTEGER,
    returned_by_name TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status, id DESC);

CREATE TABLE IF NOT EXISTS sessions (
    user_id    INTEGER PRIMARY KEY,
    state      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


def _now() -> float:
    return time.time()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)


# ------------------------------ items ------------------------------------ #

def add_item(category: str, place: str, description: str = "", photo_file: str = "",
             photo_token: str = "", user_id: int = 0, user_name: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO items (status, category, place, description, photo_file, photo_token,"
            " created_by_id, created_by_name, created_at)"
            " VALUES ('found', ?, ?, ?, ?, ?, ?, ?, ?)",
            (category, place, description, photo_file, photo_token, user_id, user_name, _now()),
        )
        return int(cur.lastrowid)


def set_photo(item_id: int, *, path: Optional[str] = None, token: Optional[str] = None) -> None:
    if path is None and token is None:
        return
    with connect() as conn:
        if path is not None:
            conn.execute("UPDATE items SET photo_file=? WHERE id=?", (path, item_id))
        if token is not None:
            conn.execute("UPDATE items SET photo_token=? WHERE id=?", (token, item_id))


def get_item(item_id: int) -> Optional[sqlite3.Row]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return row  # type: ignore[return-value]


def list_items(status: str, limit: int = 30, offset: int = 0) -> list[sqlite3.Row]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM items WHERE status=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (status, limit, offset),
        ).fetchall()
        return rows


def claim_item(item_id: int, user_id: int, user_name: str) -> Optional[sqlite3.Row]:
    """Пометить вещь возвращённой владельцу (слайд 11). Возвращает строку до/после."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            return None
        was = row["status"]
        conn.execute(
            "UPDATE items SET status='returned', returned_at=?, returned_by_id=?,"
            " returned_by_name=? WHERE id=?",
            (_now(), user_id, user_name, item_id),
        )
        new = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return {"was": was, "item": new}  # type: ignore[return-value]


def stats() -> dict[str, Any]:
    """Учёт пропаж (слайд 5): сколько всего, сколько снова у владельцев, по категориям."""
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        active = conn.execute("SELECT COUNT(*) c FROM items WHERE status='found'").fetchone()["c"]
        returned = conn.execute("SELECT COUNT(*) c FROM items WHERE status='returned'").fetchone()["c"]
        by_cat = {
            r["category"]: r["c"]
            for r in conn.execute(
                "SELECT category, COUNT(*) c FROM items GROUP BY category ORDER BY c DESC LIMIT 12"
            ).fetchall()
        }
        return {"total": total, "active": active, "returned": returned, "by_category": by_cat}


def delete_item(item_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM items WHERE id=?", (item_id,))
        return cur.rowcount > 0


# --------------------------- sessions (форма) ----------------------------- #

def set_session(user_id: int, state: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO sessions (user_id, state, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
            (user_id, json.dumps(state, ensure_ascii=False), _now()),
        )


def get_session(user_id: int) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT state FROM sessions WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["state"])
        except (ValueError, TypeError):
            return None


def drop_session(user_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
