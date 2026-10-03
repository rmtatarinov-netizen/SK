"""Восстановление из снапшота (Шаг 12).

    python scripts/restore_backup.py backups/backup_2026-10-03_120000_ab12cd [--force]

Скрипт ЧИТАЕТ контрольные суммы и проверяет БД снапшота до переноса.
Перед восстановлением остановите веб-приложение, воркер и бот.
Чек-лист проверки восстановления — docs/reliability.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import get_settings  # noqa: E402
from src.observability import configure_logging  # noqa: E402
from src.reliability import restore_backup, verify_backup  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore from backup snapshot")
    parser.add_argument("backup_dir", help="каталог backups/backup_*")
    parser.add_argument("--force", action="store_true", help="перезаписать существующую БД")
    args = parser.parse_args()

    configure_logging("restore")
    settings = get_settings()
    db_target = settings.db_path
    if db_target is None:
        print("Ожидается файловая SQLite-БД", file=sys.stderr)
        return 2

    backup_dir = Path(args.backup_dir)
    if not backup_dir.is_dir():
        print(f"Снапшот не найден: {backup_dir}", file=sys.stderr)
        return 2

    check = verify_backup(backup_dir)
    if not check.ok:
        print(
            f"Снапшот ПОВРЕЖДЁН: integrity={check.db_integrity} "
            f"missing={check.missing[:5]} mismatched={check.mismatched[:5]}",
            file=sys.stderr,
        )
        return 1
    print(f"Проверка снапшота: OK (integrity={check.db_integrity})")

    try:
        restore_backup(
            backup_dir=backup_dir,
            db_target=db_target,
            data_root=settings.resolve_path("data"),
            force=args.force,
        )
    except FileExistsError as exc:
        print(f"{exc}", file=sys.stderr)
        return 3

    print(f"Восстановлено в {db_target}. Выполните чек-лист docs/reliability.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
