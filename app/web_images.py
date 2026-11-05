"""Заготовка для сохранения web-фотографий (используется в web.py)."""
from __future__ import annotations

import secrets
from pathlib import Path

from .config import settings

_ALLOWED = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp", "gif": "image/gif"}


def save_web_photo(data: bytes, content_type: str) -> str:
    """Сохраняет файл фотографии в data/photos, возвращает имя файла."""
    ext = "jpg"
    main = (content_type or "").split(";")[0].strip().lower()
    for k, v in _ALLOWED.items():
        if v == main:
            ext = k
            break
    settings.ensure_dirs()
    name = f"{secrets.token_hex(10)}.{ext}"
    path: Path = settings.photos_dir / name
    path.write_bytes(data)
    return name
