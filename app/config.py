"""Конфигурация проекта «Бюро находок» (переменные окружения)."""
import os
from pathlib import Path

_MAX_INT = 2147483647


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        self.bot_token: str = _env("BOT_TOKEN")
        # Домен Bot API MAX. Актуальный домен — platform-api2.max.ru
        # (platform-api.max.ru устарел, см. dev.max.ru/docs-api).
        self.max_api_url: str = _env("MAX_API_URL", "https://platform-api2.max.ru")
        # Публичный URL мини-приложения (его же вписывают в настройках бота
        # в «MAX для бизнеса» как адрес мини-приложения).
        self.webapp_public_url: str = _env("WEBAPP_PUBLIC_URL", "http://localhost:8000")
        self.host: str = _env("HOST", "0.0.0.0")
        self.port: int = _env_int("PORT", 8000)
        self.data_dir: Path = Path(_env("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
        self.photos_dir: Path = self.data_dir / "photos"
        self.poll_timeout: int = min(max(_env_int("POLL_TIMEOUT", 30), 0), 90)
        self.timezone: str = _env("TZ", "Europe/Moscow")
        # На случай сетей с нестандартным TLS — форсировать отключение проверки
        # сертификатов (по умолчанию НЕ включать).
        self.tls_no_verify: bool = _env("TLS_NO_VERIFY", "0") == "1"
        # Сколько объявлений показывать в одном сообщении-списке.
        self.list_page_size: int = min(max(_env_int("LIST_PAGE_SIZE", 10), 3), 25)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.photos_dir.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "buro.db"

    @property
    def web_url(self) -> str:
        return self.webapp_public_url.rstrip("/")


settings = Settings()
