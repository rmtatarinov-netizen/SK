"""Резервная копия БД и файлов (Шаг 12).

    python scripts/backup.py                 # снапшот в backups/ с ротацией
    python scripts/backup.py --keep 14

Печатается путь созданного снапшота (удобно для планировщика).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import get_settings  # noqa: E402
from src.observability import configure_logging  # noqa: E402
from src.reliability import create_backup  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup database snapshot and files")
    parser.add_argument("--keep", type=int, default=30, help="сколько снапшотов хранить")
    parser.add_argument("--dest", default=None, help="каталог бэкапов (по умолчанию <проект>/backups)")
    args = parser.parse_args()

    configure_logging("backup")
    settings = get_settings()
    db_path = settings.db_path
    if db_path is None:
        print("Поддерживается бэкап только файловой SQLite-БД", file=sys.stderr)
        return 2
    data_root = settings.resolve_path("data")
    backups_root = Path(args.dest) if args.dest else settings.resolve_path("backups")

    if not db_path.exists():
        print(f"БД не найдена ({db_path}) — сначала примените миграции.", file=sys.stderr)
        return 2

    result = create_backup(
        db_path=db_path, data_root=data_root, backups_root=backups_root, keep=args.keep
    )
    print(result.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
