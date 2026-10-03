"""Создание движка БД и сессий. SQLite: WAL и включённые внешние ключи."""

from __future__ import annotations

from collections.abc import Iterator
from functools import cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from src.config import get_settings


@cache
def get_engine() -> Engine:
    settings = get_settings()
    engine = create_engine(settings.sqlalchemy_url, echo=settings.app.debug)

    if settings.sqlalchemy_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                if settings.db.wal:
                    cursor.execute("PRAGMA journal_mode=WAL")
            finally:
                cursor.close()

    return engine


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=engine or get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Зависимость FastAPI (Шаг 9): сессия на время HTTP-запроса."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
