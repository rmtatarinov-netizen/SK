"""Загрузка конфигурации системы: config.yaml (параметры) + .env (секреты).

Секреты не хранятся в config.yaml и не коммитятся. Все пути в конфиге
относительные — разрешаются от корня проекта (parents[1] от src/config.py).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
ENV_FILE = PROJECT_ROOT / ".env"


class AppConfig(BaseModel):
    name: str = "prescriptions_system"
    debug: bool = False


class DbConfig(BaseModel):
    url: str = "sqlite:///data/prescriptions.db"
    wal: bool = True


class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    llm_model: str = "qwen2.5:7b-instruct"
    embed_model: str = "bge-m3"
    timeout_s: int = 300


class RagConfig(BaseModel):
    collection: str = "norm_docs"
    persist_directory: str = "data/chroma"
    chunk_size_tokens: int = 500
    chunk_overlap_tokens: int = 50
    top_k: int = 5


class OcrConfig(BaseModel):
    workspace: str = "data/ocr_workspace"
    langs: list[str] = ["ru"]


class StorageConfig(BaseModel):
    inbox: str = "data/prescriptions/inbox"
    processed: str = "data/prescriptions/processed"
    storage: str = "data/prescriptions/storage"
    uploads: str = "data/uploads"

    @property
    def inbox_customer(self) -> str:
        return f"{self.inbox}/customer"


class NotificationsConfig(BaseModel):
    max_attempts: int = 5
    retry_delays_s: list[int] = [60, 300, 900, 3600]


class WebConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    session_max_age_s: int = 43200


class LoggingConfig(BaseModel):
    level: str = "INFO"
    json_logs: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app: AppConfig = AppConfig()
    db: DbConfig = DbConfig()
    ollama: OllamaConfig = OllamaConfig()
    rag: RagConfig = RagConfig()
    ocr: OcrConfig = OcrConfig()
    storage: StorageConfig = StorageConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    web: WebConfig = WebConfig()
    logging: LoggingConfig = LoggingConfig()

    # Секреты только из .env / переменных окружения.
    web_secret_key: str = "change-me-in-env"
    telegram_bot_token: str | None = None
    telegram_api_base: str = "https://api.telegram.org"
    max_api_base_url: str | None = None
    max_api_token: str | None = None

    def resolve_path(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def db_path(self) -> Path | None:
        prefix = "sqlite:///"
        if not self.db.url.startswith(prefix):
            return None
        raw = self.db.url[len(prefix):]
        if raw in ("", ":memory:"):
            return None
        return self.resolve_path(raw)

    @property
    def sqlalchemy_url(self) -> str:
        """URL SQLite с абсолютным путём (относительный зависит от CWD)."""
        db_path = self.db_path
        if db_path is None:
            return self.db.url
        return f"sqlite:///{db_path.as_posix()}"

    def ensure_directories(self) -> None:
        for relative in (
            self.storage.inbox_customer,
            self.storage.processed,
            self.storage.storage,
            self.storage.uploads,
            self.ocr.workspace,
            self.rag.persist_directory,
        ):
            self.resolve_path(relative).mkdir(parents=True, exist_ok=True)
        db_path = self.db_path
        if db_path is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


@cache
def get_settings() -> Settings:
    settings = Settings(**_load_yaml(CONFIG_PATH))
    settings.ensure_directories()
    return settings
