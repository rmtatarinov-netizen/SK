"""Надёжность (Шаг 12): резервное копирование, проверка и восстановление.

Стратегия:
- БД — консистентный снимок через sqlite3.backup() (безопасно при WAL);
- файлы — копия каталога data/ (кроме снимков БД и временного ocr_workspace);
- manifest.json с SHA-256 каждого файла и общим sha снимка БД;
- ротация: хранится последние N снапшотов;
- восстановление: проверка контрольных сумм и PRAGMA integrity_check
  ДО переноса; целевой каталог не перезаписывается без force.

Оригиналы документов (storage/inbox/processed/uploads) не удаляются никогда.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# внутри data/ не копируем: рабочие файлы снимка БД (db, db-wal, db-shm)
# и временные рабочие каталоги. Chroma (свой sqlite) — копируем полностью.
_SKIP_DIRS = {"ocr_workspace", "__pycache__"}

DB_COPY_NAME = "prescriptions.db"
MANIFEST_NAME = "manifest.json"
DATA_DIR_NAME = "data"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class BackupResult:
    path: Path
    files: int
    db_sha256: str


@dataclass
class VerifyResult:
    ok: bool
    db_integrity: str
    missing: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)


def _iter_data_files(data_root: Path, skip_files: set[Path]):
    if not data_root.exists():
        return
    for path in sorted(data_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(data_root)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if path.resolve() in skip_files:
            continue
        yield path


def snapshot_database(db_path: Path, dest_db: Path) -> str:
    """Консистентный снимок SQLite; возвращает SHA-256 снимка."""
    dest_db.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dest_db)
        try:
            source.backup(target)
            rows = target.execute("PRAGMA integrity_check").fetchall()
            status = rows[0][0] if rows else "unknown"
            if str(status).lower() != "ok":
                raise RuntimeError(f"integrity_check на снимке: {status}")
        finally:
            target.close()
    finally:
        source.close()
    return sha256_file(dest_db)


def create_backup(
    *,
    db_path: Path,
    data_root: Path,
    backups_root: Path,
    keep: int = 30,
) -> BackupResult:
    db_path = Path(db_path)
    data_root = Path(data_root)
    backups_root = Path(backups_root)
    if not db_path.exists():
        raise FileNotFoundError(f"Файл БД не найден: {db_path}")
    backups_root.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S_%f")
    target = backups_root / f"backup_{stamp}_{hashlib.sha1(db_path.name.encode()).hexdigest()[:6]}"
    target.mkdir(parents=True)

    db_sha = snapshot_database(db_path, target / DB_COPY_NAME)
    live_db = db_path.resolve()
    skip_files = {live_db, Path(str(live_db) + "-wal"), Path(str(live_db) + "-shm")}

    manifest: dict = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "db": {"name": DB_COPY_NAME, "sha256": db_sha},
        "files": {},
    }
    count = 0
    for path in _iter_data_files(data_root, skip_files):
        rel = path.relative_to(data_root)
        dest = target / DATA_DIR_NAME / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        manifest["files"][rel.as_posix()] = sha256_file(dest)
        count += 1

    (target / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _rotate(backups_root, keep)
    return BackupResult(path=target, files=count, db_sha256=db_sha)


def _rotate(backups_root: Path, keep: int) -> None:
    if keep <= 0:
        return
    snapshots = sorted(
        (p for p in backups_root.glob("backup_*") if p.is_dir()),
        key=lambda p: p.name,
    )
    for old in snapshots[:-keep]:
        shutil.rmtree(old, ignore_errors=True)


def list_backups(backups_root: Path) -> list[Path]:
    return sorted(
        (p for p in Path(backups_root).glob("backup_*") if p.is_dir()),
        key=lambda p: p.name,
    )


def verify_backup(backup_dir: Path) -> VerifyResult:
    backup_dir = Path(backup_dir)
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"Нет manifest.json в {backup_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    db_file = backup_dir / DB_COPY_NAME
    if not db_file.exists():
        return VerifyResult(False, "missing", missing=[DB_COPY_NAME])
    conn = sqlite3.connect(db_file)
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    finally:
        conn.close()
    db_integrity = str(rows[0][0] if rows else "unknown")

    missing: list[str] = []
    mismatched: list[str] = []
    for rel, expected in manifest["files"].items():
        candidate = backup_dir / DATA_DIR_NAME / rel
        if not candidate.exists():
            missing.append(rel)
        elif sha256_file(candidate) != expected:
            mismatched.append(rel)
    db_ok = manifest["db"]["sha256"] == sha256_file(db_file)
    ok = db_integrity.lower() == "ok" and db_ok and not missing and not mismatched
    return VerifyResult(ok, db_integrity, missing, mismatched)


def restore_backup(
    *,
    backup_dir: Path,
    db_target: Path,
    data_root: Path,
    force: bool = False,
) -> None:
    """Восстановление из проверенного снапшота. Целевой каталог не затирается
    без force; при force существующая БД переименовывается в *.backup-<ts>."""
    result = verify_backup(backup_dir)
    if not result.ok:
        raise RuntimeError(
            f"Проверка снапшота не пройдена: integrity={result.db_integrity} "
            f"missing={result.missing[:5]} mismatched={result.mismatched[:5]}"
        )

    backup_dir = Path(backup_dir)
    db_target = Path(db_target)
    data_root = Path(data_root)
    db_target.parent.mkdir(parents=True, exist_ok=True)
    if db_target.exists():
        if not force:
            raise FileExistsError(
                f"{db_target} существует; перезапись только с force=True "
                f"(остановите приложение перед восстановлением)."
            )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        db_target.rename(db_target.with_suffix(f".backup-{stamp}.db"))

    shutil.copy2(backup_dir / DB_COPY_NAME, db_target)

    source_data = backup_dir / DATA_DIR_NAME
    if source_data.exists():
        for path in source_data.rglob("*"):
            if path.is_file():
                rel = path.relative_to(source_data)
                dest = data_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
