"""Pydantic-схемы предписаний (Шаг 4)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from src.db.models import PrescriptionStatus, PrescriptionType


class PrescriptionItemCreate(BaseModel):
    text: str = Field(min_length=2, max_length=20000)
    deadline: date | None = None
    explicit_norm_refs: list[str] = Field(default_factory=list)


class PrescriptionCreate(BaseModel):
    prescription_type: PrescriptionType = PrescriptionType.INTERNAL
    doc_number: str = Field(min_length=1, max_length=64)
    doc_date: date
    object_id: int
    contractor_id: int
    description: str = Field(min_length=2, max_length=50000)
    deadline: date | None = None
    responsible_user_id: int | None = None
    inspector_user_id: int | None = None
    items: list[PrescriptionItemCreate] = Field(default_factory=list)


class StatusChange(BaseModel):
    new_status: PrescriptionStatus
    comment: str | None = Field(default=None, max_length=20000)
