"""Фикстуры pytest: SQLite в памяти + фабрики объектов/подрядчиков/пользователей."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db import models as m
from src.db.base import Base


@pytest.fixture()
def engine() -> Iterator[Engine]:
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def obj(db: Session) -> m.Object:
    o = m.Object(name="Склад №3", address="ул. Строителей, 1")
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


@pytest.fixture()
def contractor(db: Session) -> m.Contractor:
    c = m.Contractor(name="ООО СтройМонтаж", inn="7712345678")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture()
def admin(db: Session) -> m.User:
    u = m.User(
        login="admin1",
        full_name="Администратор Системы",
        role=m.UserRole.ADMIN,
        password_hash="x",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def inspector(db: Session, obj: m.Object) -> m.User:
    u = m.User(
        login="insp1",
        full_name="Иванов Иван",
        role=m.UserRole.INSPECTOR,
        password_hash="x",
        object_id=obj.id,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def contractor_user(db: Session, contractor: m.Contractor) -> m.User:
    u = m.User(
        login="podr1",
        full_name="Петров Пётр",
        role=m.UserRole.CONTRACTOR_RESPONSIBLE,
        password_hash="x",
        contractor_id=contractor.id,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def prescription(db: Session, obj: m.Object, contractor: m.Contractor) -> m.Prescription:
    p = m.Prescription(
        prescription_type=m.PrescriptionType.CUSTOMER,
        doc_number="П-14/2026",
        doc_date=date(2026, 9, 1),
        object_id=obj.id,
        contractor_id=contractor.id,
        description="Не обеспечена защита арматуры от коррозии.",
        deadline=date(2026, 9, 30),
        status=m.PrescriptionStatus.NEW,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def tmp_storage(tmp_path, monkeypatch):
    """Перенос файловых каталогов print/scan/ocr сервисов в tmp_path."""
    from types import SimpleNamespace

    from src.ocr import pipeline as ocr_pipeline
    from src.prescriptions import print_service, signed_scan_service

    class FakeSettings:
        storage = SimpleNamespace(
            storage="storage",
            uploads="uploads",
            processed="processed",
            inbox="inbox",
        )

        def resolve_path(self, relative):
            p = Path(relative)
            return p if p.is_absolute() else tmp_path / p

    fake = FakeSettings()
    monkeypatch.setattr(print_service, "get_settings", lambda: fake)
    monkeypatch.setattr(signed_scan_service, "get_settings", lambda: fake)
    monkeypatch.setattr(ocr_pipeline, "get_settings", lambda: fake)
    return tmp_path


@pytest.fixture()
def internal_prescription(db, obj, contractor, admin) -> m.Prescription:
    from src.prescriptions.schemas import PrescriptionCreate
    from src.prescriptions.service import create_prescription

    data = PrescriptionCreate(
        prescription_type=m.PrescriptionType.INTERNAL,
        doc_number="ВН-3/2026",
        doc_date=date(2026, 9, 2),
        object_id=obj.id,
        contractor_id=contractor.id,
        description="Отсутствует ограждение перепада высот.",
        deadline=date(2026, 9, 15),
        items=[{"text": "Установить ограждение", "deadline": None}],
    )
    return create_prescription(db, data, actor_user_id=admin.id)
