"""Экспорт DDL текущей ORM-схемы в docs/schema.sql (для аудита, Шаг 2).

Запуск из корня проекта:  python scripts/export_schema.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from src.db import models  # регистрация моделей  # noqa: F401
from src.db.base import Base


def main() -> None:
    root = pathlib.Path(__file__).resolve().parents[1]
    out = root / "docs" / "schema.sql"
    dialect = sqlite.dialect()

    parts: list[str] = [
        "-- DDL схемы prescriptions_system (SQLite), сгенерировано из ORM-моделей.",
        "-- Источник: src/db/models.py + первая ревизия Alembic. Не редактировать вручную.",
        "-- PRAGMA для подключения: journal_mode=WAL; foreign_keys=ON;",
        "",
    ]
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect)).strip().rstrip(";")
        parts.append(ddl + ";")
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            idx = str(CreateIndex(index).compile(dialect=dialect)).strip().rstrip(";")
            parts.append(idx + ";")
        parts.append("")

    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"Экспортировано таблиц: {len(Base.metadata.tables)} -> {out}")


if __name__ == "__main__":
    main()
