"""Запросы метрик отчётов (Шаг 11).

Отчёты строятся ИСКЛЮЧИТЕЛЬНО запросами к БД (запрет ТЗ на ручные формулы).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from src.db.models import (
    Contractor,
    Notification,
    NotificationStatus,
    Object,
    Prescription,
    PrescriptionHistory,
    PrescriptionNormRef,
    PrescriptionStatus,
    PrescriptionType,
)

OPEN_STATUSES = (
    PrescriptionStatus.NEW,
    PrescriptionStatus.UNDER_REVIEW,
    PrescriptionStatus.IN_WORK,
    PrescriptionStatus.FIXED_PENDING_REVIEW,
)


def total_prescriptions(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(Prescription)) or 0)


def type_breakdown(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(Prescription.prescription_type, func.count())
        .group_by(Prescription.prescription_type)
    ).all()
    return {str(pt.value): int(count) for pt, count in rows}


def status_breakdown(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(Prescription.status, func.count()).group_by(Prescription.status)
    ).all()
    return {str(st.value): int(count) for st, count in rows}


def overdue_count(db: Session, today: date | None = None) -> int:
    today = today or date.today()
    return int(
        db.scalar(
            select(func.count())
            .select_from(Prescription)
            .where(
                Prescription.status.in_(OPEN_STATUSES),
                Prescription.deadline.is_not(None),
                Prescription.deadline < today,
            )
        )
        or 0
    )


def average_fix_days(db: Session) -> float | None:
    """Средний срок устранения: created_at → closed_at для закрытых."""
    value = db.scalar(
        select(
            func.avg(
                func.julianday(Prescription.closed_at)
                - func.julianday(Prescription.created_at)
            )
        ).where(
            Prescription.status == PrescriptionStatus.CLOSED,
            Prescription.closed_at.is_not(None),
        )
    )
    return round(float(value), 2) if value is not None else None


def average_review_days(db: Session) -> float | None:
    """Среднее время до первой проверки (created_at → первый under_review)."""
    first_review = (
        select(
            PrescriptionHistory.prescription_id.label("pid"),
            func.min(PrescriptionHistory.created_at).label("review_at"),
        )
        .where(
            PrescriptionHistory.change_type == "status_changed",
            func.json_extract(PrescriptionHistory.new_value, "$.status")
            == PrescriptionStatus.UNDER_REVIEW.value,
        )
        .group_by(PrescriptionHistory.prescription_id)
        .subquery()
    )
    value = db.scalar(
        select(
            func.avg(
                func.julianday(first_review.c.review_at)
                - func.julianday(Prescription.created_at)
            )
        ).select_from(Prescription).join(
            first_review, first_review.c.pid == Prescription.id
        )
    )
    return round(float(value), 2) if value is not None else None


def rework_count(db: Session) -> int:
    """Возвраты на доработку: fixed_pending_review → in_work."""
    return int(
        db.scalar(
            select(func.count())
            .select_from(PrescriptionHistory)
            .where(
                PrescriptionHistory.change_type == "status_changed",
                func.json_extract(PrescriptionHistory.old_value, "$.status")
                == PrescriptionStatus.FIXED_PENDING_REVIEW.value,
                func.json_extract(PrescriptionHistory.new_value, "$.status")
                == PrescriptionStatus.IN_WORK.value,
            )
        )
        or 0
    )


def by_contractor(db: Session) -> list[list]:
    rows = db.execute(
        select(Contractor.name, func.count(Prescription.id))
        .join(Prescription, Prescription.contractor_id == Contractor.id)
        .group_by(Contractor.name)
        .order_by(func.count(Prescription.id).desc())
    ).all()
    return [[name, int(count)] for name, count in rows]


def by_object(db: Session) -> list[list]:
    rows = db.execute(
        select(Object.name, func.count(Prescription.id))
        .join(Prescription, Prescription.object_id == Object.id)
        .group_by(Object.name)
        .order_by(func.count(Prescription.id).desc())
    ).all()
    return [[name, int(count)] for name, count in rows]


def norm_frequency(db: Session, limit: int = 20) -> list[dict]:
    rows = db.execute(
        select(
            PrescriptionNormRef.doc_code,
            PrescriptionNormRef.doc_item,
            func.count().label("cnt"),
        )
        .where(PrescriptionNormRef.status == "confirmed")
        .group_by(PrescriptionNormRef.doc_code, PrescriptionNormRef.doc_item)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return [
        {"doc_code": code, "item": item, "count": int(cnt)}
        for code, item, cnt in rows
    ]


def notification_stats(db: Session) -> dict:
    rows = db.execute(
        select(Notification.status, func.count()).group_by(Notification.status)
    ).all()
    counts = {str(st.value): int(count) for st, count in rows}
    total = sum(counts.values())
    sent = counts.get("sent", 0)
    failed = counts.get("failed", 0)
    return {
        "total": total,
        "by_status": counts,
        "sent_pct": round(sent * 100 / total, 1) if total else None,
        "failed_pct": round(failed * 100 / total, 1) if total else None,
    }


def registry_rows(db: Session) -> list[dict]:
    """Плоские строки для CSV/XLSX-экспорта."""
    today = date.today()
    prescriptions = db.scalars(
        select(Prescription).order_by(Prescription.created_at)
    ).all()
    rows = []
    for p in prescriptions:
        rows.append(
            {
                "id": p.id,
                "doc_number": p.doc_number,
                "doc_date": p.doc_date.isoformat(),
                "type": p.prescription_type.value,
                "object": p.object.name if p.object else "",
                "contractor": p.contractor.name if p.contractor else "",
                "status": p.status.value,
                "deadline": p.deadline.isoformat() if p.deadline else "",
                "overdue": bool(
                    p.deadline
                    and p.deadline < today
                    and p.status in OPEN_STATUSES
                ),
                "requires_review": bool(p.requires_review),
                "confirmed_at": p.confirmed_at.isoformat(sep=" ") if p.confirmed_at else "",
                "closed_at": p.closed_at.isoformat(sep=" ") if p.closed_at else "",
                "created_at": p.created_at.isoformat(sep=" ") if p.created_at else "",
            }
        )
    return rows
