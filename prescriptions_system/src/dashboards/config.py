"""Веса риск-балла и пороги уровней (ТЗ 01 §6, 03 §4; хранятся в system_config).

Ключ M8: dash.risk_weights. Значения по умолчанию соответствуют шкале из ТЗ.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import SystemConfig

WEIGHTS_KEY = "dash.risk_weights"

DEFAULT_RISK_WEIGHTS: dict[str, int | float] = {
    "category_a": 50,
    "category_b": 25,
    "category_c": 10,
    "overdue_gt14": 30,
    "overdue_1_14": 20,
    "due_le3": 15,
    "due_le7": 8,
    "repeat": 20,
    "no_responsible": 15,
    "no_deadline": 15,
    "no_norm_ref": 10,
    "work_risk_max": 10,
}

RISK_CRITICAL = 70
RISK_HIGH = 40
RISK_MEDIUM = 20

CATEGORY_GRADES = {
    "a": ("а", "a", "категория а", "критическое"),
    "b": ("в", "b", "категория в", "значимое"),
    "c": ("с", "c", "категория с", "локальное"),
}


def get_risk_weights(db: Session) -> dict[str, int | float]:
    """Веса из system_config с дополнением значениями по умолчанию."""
    stored = db.scalar(select(SystemConfig.value).where(SystemConfig.key == WEIGHTS_KEY))
    weights = dict(DEFAULT_RISK_WEIGHTS)
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in weights and isinstance(value, (int, float)):
                weights[key] = value
    return weights


def set_risk_weights(db: Session, weights: dict) -> None:
    row = db.scalar(select(SystemConfig).where(SystemConfig.key == WEIGHTS_KEY))
    if row is None:
        row = SystemConfig(key=WEIGHTS_KEY, description="Веса риск-балла дашбордов")
        db.add(row)
    row.value = {k: v for k, v in weights.items() if k in DEFAULT_RISK_WEIGHTS}


def category_id_map(db: Session) -> dict[str, int]:
    """grade ('a'|'b'|'c') → category_id по справочнику prescription_categories."""
    from src.db.models import PrescriptionCategory

    mapping: dict[str, int] = {}
    for cat in db.scalars(select(PrescriptionCategory)):
        norm = (cat.name or "").strip().lower()
        for grade, markers in CATEGORY_GRADES.items():
            if grade in mapping:
                continue
            if norm in markers or any(norm.startswith(m) for m in markers):
                mapping[grade] = cat.id
                break
    return mapping
