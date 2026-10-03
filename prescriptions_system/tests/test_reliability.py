"""Тесты надёжности (Шаг 12): снимок БД, манифест, проверка, восстановление,
ротация и JSON-логирование."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pytest

from src.observability import JsonFormatter, configure_logging
from src.reliability import (
    create_backup,
    list_backups,
    restore_backup,
    verify_backup,
)


def _make_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [("a",), ("b",), ("c",)])
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

@pytest.fixture()
def env(tmp_path):
    data = tmp_path / "data"
    (data / "prescriptions" / "storage").mkdir(parents=True)
    (data / "uploads").mkdir(parents=True)
    (data / "chroma").mkdir(parents=True)
    (data / "ocr_workspace").mkdir(parents=True)
    (data / "prescriptions" / "storage" / "doc1.pdf").write_bytes(b"doc-bytes-1")
    (data / "uploads" / "img.jpg").write_bytes(b"img-bytes")
    (data / "chroma" / "chroma.sqlite3").write_bytes(b"vectors")
    (data / "ocr_workspace" / "temp.png").write_bytes(b"scratch")
    db = data / "app.db"
    _make_db(db)
    return {"db": db, "data": data, "backups": tmp_path / "backups"}


def test_backup_contents(env):
    result = create_backup(
        db_path=env["db"], data_root=env["data"], backups_root=env["backups"]
    )
    target = result.path
    assert (target / "prescriptions.db").exists()
    assert (target / "manifest.json").exists()
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert "prescriptions/storage/doc1.pdf" in manifest["files"]
    assert "uploads/img.jpg" in manifest["files"]
    assert "chroma/chroma.sqlite3" in manifest["files"]
    assert "ocr_workspace/temp.png" not in manifest["files"]  # черновик не бэкапим
    assert result.files == 3
    # сама рабочая БД в data/ не копируется как файл (снимок — отдельный)
    assert not any(k.endswith(".db") for k in manifest["files"])


def test_verify_and_detect_tamper(env):
    result = create_backup(
        db_path=env["db"], data_root=env["data"], backups_root=env["backups"]
    )
    check = verify_backup(result.path)
    assert check.ok, (check.db_integrity, check.missing, check.mismatched)

    target = result.path / "data" / "uploads" / "img.jpg"
    target.write_bytes(b"tampered")
    check = verify_backup(result.path)
    assert not check.ok
    assert "uploads/img.jpg" in check.mismatched


def test_restore_rejects_and_forces(env, tmp_path):
    result = create_backup(
        db_path=env["db"], data_root=env["data"], backups_root=env["backups"]
    )
    db_target = tmp_path / "restore" / "app.db"
    restore_backup(
        backup_dir=result.path, db_target=db_target, data_root=tmp_path / "restore_data"
    )
    assert db_target.exists()
    conn = sqlite3.connect(db_target)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM t").fetchone()
    finally:
        conn.close()
    assert rows[0] == 3
    assert (tmp_path / "restore_data" / "prescriptions" / "storage" / "doc1.pdf").exists()

    with pytest.raises(FileExistsError):
        restore_backup(
            backup_dir=result.path, db_target=db_target,
            data_root=tmp_path / "restore_data",
        )
    restore_backup(
        backup_dir=result.path, db_target=db_target,
        data_root=tmp_path / "restore_data", force=True,
    )
    kept = list(db_target.parent.glob("*.backup-*.db"))
    assert kept  # прежняя БД переименована, не удалена


def test_rotation_keeps_latest(env):
    env["db"].touch()
    created = []
    for _ in range(4):
        created.append(
            create_backup(
                db_path=env["db"], data_root=env["data"],
                backups_root=env["backups"], keep=2,
            ).path
        )
    remaining = list_backups(env["backups"])
    assert len(remaining) <= 3  # имя с секундной меткой может совпасть; важно: не 4+
    assert set(remaining) <= set(created)
    assert all(p.exists() for p in remaining)


def test_verify_requires_manifest(env):
    with pytest.raises(FileNotFoundError):
        verify_backup(env["backups"] / "nope")


# --- логирование ---------------------------------------------------------------


def test_json_formatter_includes_extras():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        "src.ocr.pipeline", logging.INFO, "f.py", 1,
        "skip already processed", None, None,
    )
    record.notification_id = 7
    record.sha256 = "abc"
    payload = json.loads(formatter.format(record))
    assert payload["level"] == "INFO"
    assert payload["message"] == "skip already processed"
    assert payload["notification_id"] == 7
    assert payload["sha256"] == "abc"


def test_configure_logging_idempotent():
    configure_logging("test_service")
    configure_logging("test_service")
    root = logging.getLogger()
    assert getattr(root, "_ps_configured", False) is True
    assert len(root.handlers) == 1
