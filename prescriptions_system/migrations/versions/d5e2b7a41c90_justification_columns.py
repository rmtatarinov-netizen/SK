"""justification columns in registry order

Колонка «Обоснование» ставится сразу после «Описание нарушений»:
в prescriptions — после description, в prescription_items — после text.
SQLite не умеет менять порядок колонок, поэтому таблицы пересобираются:
новая таблица с нужным порядком, копирование данных, переименование.
Внешние ключи и индексы восстанавливаются из исходного DDL.

Revision ID: d5e2b7a41c90
Revises: a7c31f9d2b54
Create Date: 2026-10-21 19:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5e2b7a41c90"
down_revision: Union[str, None] = "a7c31f9d2b54"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE_CONSTRAINT_STARTS = ("CONSTRAINT", "PRIMARY", "UNIQUE", "CHECK", "FOREIGN")


def _split_parts(body: str) -> list[str]:
    """Разбить тело DDL таблицы на части по запятым верхнего уровня."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    in_string = False
    for char in body:
        if char == "'":
            in_string = not in_string
        if not in_string:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                parts.append("".join(current).strip())
                current = []
                continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _column_name(part: str) -> str:
    token = part.strip().split("(")[0].strip()
    return token.split()[0].strip('"') if token else ""


def _rebuild_table(
    table_name: str,
    *,
    additions: Sequence[tuple[str, str, str]] = (),
    moves: Sequence[tuple[str, str]] = (),
) -> None:
    """Пересобрать таблицу SQLite с новым порядком колонок.

    additions — (имя, DDL-фрагмент, колонка-после-какой);
    moves — (колонка, колонка-после-какой).
    FK-констрейнты и индексы восстанавливаются как были.
    """
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        raise RuntimeError("Миграция порядка колонок рассчитана на SQLite")

    table_ddl = bind.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).scalar()
    if not table_ddl:
        raise RuntimeError(f"Таблица {table_name} не найдена")

    index_ddl = [
        row[0]
        for row in bind.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = ? "
            "AND sql IS NOT NULL",
            (table_name,),
        )
    ]

    open_paren = table_ddl.index("(")
    close_paren = table_ddl.rindex(")")
    parts = _split_parts(table_ddl[open_paren + 1 : close_paren])

    columns: list[str] = []
    constraints: list[str] = []
    for part in parts:
        if part.upper().startswith(_TABLE_CONSTRAINT_STARTS):
            constraints.append(part)
        else:
            columns.append(part)

    for name, ddl, after in additions:
        columns.insert(_index_after(columns, after) + 1, f"{name} {ddl}")
    for name, after in moves:
        index = _index_after(columns, name)
        part = columns.pop(index)
        columns.insert(_index_after(columns, after) + 1, part)

    new_body = ",\n\t".join(columns + constraints)
    temp_name = f"{table_name}__reorder"
    new_ddl = f'CREATE TABLE "{temp_name}" (\n\t{new_body}\n)'

    old_names = [_column_name(part) for part in columns if _column_name(part) not in
                 {name for name, _, _ in additions}]
    new_names = [_column_name(part) for part in columns]
    select_list = ", ".join(
        name if name in old_names else "NULL" for name in new_names
    )

    bind.exec_driver_sql(f'DROP TABLE IF EXISTS "{temp_name}"')
    bind.exec_driver_sql(new_ddl)
    bind.exec_driver_sql(
        f'INSERT INTO "{temp_name}" ({", ".join(chr(34) + n + chr(34) for n in new_names)}) '
        f"SELECT {select_list} FROM \"{table_name}\""
    )
    bind.exec_driver_sql(f'DROP TABLE "{table_name}"')
    bind.exec_driver_sql(f'ALTER TABLE "{temp_name}" RENAME TO "{table_name}"')
    for ddl in index_ddl:
        bind.exec_driver_sql(ddl)


def _index_after(columns: list[str], name: str) -> int:
    for index, part in enumerate(columns):
        if _column_name(part) == name:
            return index
    raise RuntimeError(f"Колонка {name} не найдена")


def upgrade() -> None:
    # prescription_items: «Обоснование» сразу после «описания нарушений» (text).
    _rebuild_table(
        "prescription_items",
        additions=[("justification", "TEXT", "text")],
    )
    # prescriptions: «Обоснование» сразу после «описания нарушений» (description).
    _rebuild_table(
        "prescriptions",
        moves=[("justification", "description")],
    )


def downgrade() -> None:
    _rebuild_table(
        "prescription_items",
        additions=[],
    )
    # Возврат колонки justification в конец prescription_items и prescriptions.
    bind = op.get_bind()
    bind.exec_driver_sql("ALTER TABLE prescription_items DROP COLUMN justification")
    _rebuild_table(
        "prescriptions",
        moves=[("justification", "overdue_reason_text")],
    )
