"""Роутер дашбордов (ТЗ docs/dashboards): /dash/executive + JSON-данные.

Права как у отчётов: admin/inspector/manager (CAN_VIEW из reports.py).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.dashboards import metrics
from src.db.models import (
    Contractor,
    ConstructionManagement,
    Object,
    PrescriptionCategory,
    PrescriptionKind,
    Section,
    User,
    UserRole,
    WorkType,
)
from src.web.deps import get_db, render
from src.web.security import require_role

router = APIRouter(prefix="/dash", tags=["dashboards"])

CAN_VIEW = require_role(UserRole.ADMIN, UserRole.INSPECTOR, UserRole.MANAGER)


def _filter_options(db: Session) -> dict:
    return {
        "objects": db.scalars(select(Object).order_by(Object.name)).all(),
        "sections": db.scalars(select(Section).order_by(Section.name)).all(),
        "contractors": db.scalars(select(Contractor).order_by(Contractor.name)).all(),
        "mgmt": db.scalars(select(ConstructionManagement).order_by(
            ConstructionManagement.name)).all(),
        "categories": db.scalars(select(PrescriptionCategory).order_by(
            PrescriptionCategory.name)).all(),
        "kinds": db.scalars(select(PrescriptionKind).order_by(
            PrescriptionKind.name)).all(),
        "work_types": db.scalars(select(WorkType).where(WorkType.is_active.is_(True)).order_by(
            WorkType.name)).all(),
        "responsible_users": db.scalars(
            select(User).where(User.is_active.is_(True)).order_by(User.full_name)
        ).all(),
    }


@router.get("/executive")
def executive(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(CAN_VIEW),
):
    params = dict(request.query_params)
    data = metrics.build_executive(db, params)
    return render(
        request,
        "dashboards/executive.html",
        user=user,
        data=data,
        data_json=json.dumps(data, ensure_ascii=False),
        options=_filter_options(db),
        active=dict(params),
    )


@router.get("/executive/data")
def executive_data(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(CAN_VIEW),
):
    return JSONResponse(metrics.build_executive(db, dict(request.query_params)))
