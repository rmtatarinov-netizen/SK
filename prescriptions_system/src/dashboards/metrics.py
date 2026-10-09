"""Метрики дашборда 01 «Панель руководителя» (docs/dashboards/01_*.md).

Единый источник предикатов для KPI и drill-down реестра: и дашборд, и
/prescriptions используют функции is_open/is_overdue/..., поэтому число на
карточке всегда равно числу строк в реестре по клику (критерий приемки №2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import literal, select
from sqlalchemy.orm import Session

from src.dashboards.config import (
    RISK_CRITICAL,
    RISK_HIGH,
    RISK_MEDIUM,
    category_id_map,
    get_risk_weights,
)
from src.db.models import (
    Contractor,
    NormRefStatus,
    Object,
    Prescription,
    PrescriptionCategory,
    PrescriptionKind,
    PrescriptionNormRef,
    PrescriptionStatus,
    Section,
    ConstructionManagement,
    User,
    WorkType,
)

OPEN_STATUSES = {
    PrescriptionStatus.NEW,
    PrescriptionStatus.UNDER_REVIEW,
    PrescriptionStatus.IN_WORK,
    PrescriptionStatus.FIXED_PENDING_REVIEW,
}

PERIOD_PRESETS = {"week": 7, "month": 30, "quarter": 91, "year": 365, "all": 100_000}


def _to_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _to_int(value) -> int | None:
    try:
        return int(value) if value not in (None, "", "none") else None
    except (TypeError, ValueError):
        return None


@dataclass
class Filters:
    period_type: str = ""
    date_from: date | None = None
    date_to: date | None = None
    object_id: int | None = None
    section_id: int | None = None
    contractor_id: int | None = None
    mgmt_id: int | None = None
    category_id: int | None = None
    kind_id: int | None = None
    work_type_id: int | None = None
    responsible_id: int | None = None
    status: str = ""
    include_drafts: bool = False
    raw: dict = field(default_factory=dict)

    @property
    def bounds(self) -> tuple[date, date]:
        end = self.date_to or date.today()
        if self.date_from:
            return self.date_from, end
        days = PERIOD_PRESETS.get(self.period_type, 30)
        return end - timedelta(days=days - 1), end


def parse_filters(params: dict) -> Filters:
    f = Filters(
        period_type=(params.get("period_type") or "").strip(),
        date_from=_to_date(params.get("date_from")),
        date_to=_to_date(params.get("date_to")),
        object_id=_to_int(params.get("object_id")),
        section_id=_to_int(params.get("section_id")),
        contractor_id=_to_int(params.get("contractor_id")),
        mgmt_id=_to_int(params.get("mgmt_id")),
        category_id=_to_int(params.get("category_id")),
        kind_id=_to_int(params.get("kind_id")),
        work_type_id=_to_int(params.get("work_type_id")),
        responsible_id=_to_int(params.get("responsible_id")),
        status=(params.get("status") or "").strip(),
        include_drafts=str(params.get("include_drafts") or "") in ("1", "true", "on"),
    )
    f.raw = dict(params)
    return f


# --- предикаты (общие для дашборда и реестра) -------------------------------

def issued_in_period(r: dict, f: Filters) -> bool:
    d = r["issue_date"] or r["doc_date"]
    start, end = f.bounds
    return d is not None and start <= d <= end


def is_open(r: dict) -> bool:
    return r["status"] in {s.value for s in OPEN_STATUSES}


def is_draft(r: dict) -> bool:
    return bool(r["requires_review"])


def closed_date(r: dict) -> date | None:
    return r["actual_fix_date"] or r["closed_at"]


def fix_date_in_period(r: dict, f: Filters) -> bool:
    cd = closed_date(r)
    start, end = f.bounds
    return cd is not None and start <= cd <= end and r["status"] == "closed"


def is_overdue(r: dict, today: date) -> bool:
    return (
        is_open(r)
        and r["deadline"] is not None
        and r["deadline"] < today
    )


def days_overdue(r: dict, today: date) -> int:
    if not is_overdue(r, today):
        return 0
    return (today - r["deadline"]).days


def due_in_days(r: dict, today: date) -> int | None:
    if not is_open(r) or r["deadline"] is None:
        return None
    return (r["deadline"] - today).days


def fixed_on_time(r: dict) -> bool:
    cd = closed_date(r)
    return cd is not None and r["deadline"] is not None and cd <= r["deadline"]


def fix_days(r: dict) -> int | None:
    cd = closed_date(r)
    base = r["issue_date"] or r["doc_date"]
    if cd is None or base is None:
        return None
    return (cd - base).days


def age_days(r: dict, today: date) -> int | None:
    if not is_open(r):
        return None
    base = r["issue_date"] or r["doc_date"]
    return (today - base).days if base else None


# --- выборка ---------------------------------------------------------------

def fetch_rows(db: Session, f: Filters) -> list[dict]:
    stmt = (
        select(
            Prescription,
            Object.name.label("object_name"),
            Contractor.name.label("contractor_name"),
            PrescriptionCategory.name.label("category_name"),
            PrescriptionKind.name.label("kind_name"),
            WorkType.name.label("work_type_name"),
            Section.name.label("section_name"),
            ConstructionManagement.name.label("mgmt_name"),
            User.full_name.label("responsible_name"),
        )
        .join(Object, Prescription.object_id == Object.id, isouter=True)
        .join(Contractor, Prescription.contractor_id == Contractor.id, isouter=True)
        .join(PrescriptionCategory, Prescription.category_id == PrescriptionCategory.id, isouter=True)
        .join(PrescriptionKind, Prescription.kind_id == PrescriptionKind.id, isouter=True)
        .join(WorkType, Prescription.work_type_id == WorkType.id, isouter=True)
        .join(Section, Prescription.section_id == Section.id, isouter=True)
        .join(ConstructionManagement, Prescription.mgmt_id == ConstructionManagement.id, isouter=True)
        .join(User, Prescription.responsible_user_id == User.id, isouter=True)
    )
    if f.object_id:
        stmt = stmt.where(Prescription.object_id == f.object_id)
    if f.section_id:
        stmt = stmt.where(Prescription.section_id == f.section_id)
    if f.contractor_id:
        stmt = stmt.where(Prescription.contractor_id == f.contractor_id)
    if f.mgmt_id:
        stmt = stmt.where(Prescription.mgmt_id == f.mgmt_id)
    if f.category_id:
        stmt = stmt.where(Prescription.category_id == f.category_id)
    if f.kind_id:
        stmt = stmt.where(Prescription.kind_id == f.kind_id)
    if f.work_type_id:
        stmt = stmt.where(Prescription.work_type_id == f.work_type_id)
    if f.responsible_id:
        stmt = stmt.where(Prescription.responsible_user_id == f.responsible_id)
    if f.status:
        stmt = stmt.where(Prescription.status == f.status)

    confirmed = {
        row for row in db.scalars(
            select(PrescriptionNormRef.prescription_id)
            .where(PrescriptionNormRef.status == NormRefStatus.CONFIRMED)
            .distinct()
        )
    }

    rows: list[dict] = []
    for (
        p, object_name, contractor_name, category_name, kind_name,
        work_type_name, section_name, mgmt_name, responsible_name,
    ) in db.execute(stmt):
        rows.append(
            {
                "id": p.id,
                "doc_number": p.doc_number,
                "prescription_type": p.prescription_type.value,
                "doc_date": p.doc_date,
                "issue_date": p.issue_date,
                "deadline": p.deadline,
                "actual_fix_date": p.actual_fix_date,
                "closed_at": p.closed_at.date() if p.closed_at else None,
                "updated_at": p.updated_at.date() if p.updated_at else None,
                "status": p.status.value,
                "requires_review": p.requires_review,
                "object_id": p.object_id,
                "object_name": object_name or "—",
                "contractor_name": contractor_name or "—",
                "category_id": p.category_id,
                "category_name": category_name or "—",
                "kind_id": p.kind_id,
                "kind_name": kind_name,
                "work_type_id": p.work_type_id,
                "work_name": work_type_name or kind_name or "—",
                "section_name": section_name,
                "mgmt_name": mgmt_name or "—",
                "responsible_id": p.responsible_user_id,
                "responsible_name": responsible_name,
                "description": p.description or "",
                "recurrence_of_id": p.recurrence_of_id,
                "has_confirmed_ref": p.id in confirmed,
            }
        )
    return rows


def scope_rows(db: Session, f: Filters) -> list[dict]:
    """Строки в отобранном периоде без черновиков (когорта KPI)."""
    rows = fetch_rows(db, f)
    if not f.include_drafts:
        rows = [r for r in rows if not is_draft(r)]
    return [r for r in rows if issued_in_period(r, f)]


# --- риск-балл (ТЗ 01 §6 / 03 §4) ------------------------------------------

def risk_score(r: dict, weights: dict, grade_by_cat: dict, today: date) -> int:
    grade = grade_by_cat.get(r["category_id"])
    score = int(weights.get(f"category_{grade}", 0)) if grade else 0

    od = days_overdue(r, today)
    if od > 14:
        score += int(weights["overdue_gt14"])
    elif od >= 1:
        score += int(weights["overdue_1_14"])
    else:
        d = due_in_days(r, today)
        if d is not None and 0 <= d <= 3:
            score += int(weights["due_le3"])
        elif d is not None and 4 <= d <= 7:
            score += int(weights["due_le7"])

    if r["recurrence_of_id"] is not None:
        score += int(weights["repeat"])
    if is_open(r) and r["responsible_id"] is None:
        score += int(weights["no_responsible"])
    if is_open(r) and r["deadline"] is None:
        score += int(weights["no_deadline"])
    if is_open(r) and not r["has_confirmed_ref"]:
        score += int(weights["no_norm_ref"])
    return score


def risk_band(score: int) -> str:
    if score >= RISK_CRITICAL:
        return "critical"
    if score >= RISK_HIGH:
        return "high"
    if score >= RISK_MEDIUM:
        return "medium"
    return "controlled"


# --- агрегаты --------------------------------------------------------------

def compute_kpis(rows: list[dict], f: Filters, prev_rows: list[dict], today: date) -> dict:
    open_rows = [r for r in rows if is_open(r)]
    overdue = [r for r in open_rows if is_overdue(r, today)]
    due7 = [r for r in open_rows if (d := due_in_days(r, today)) is not None and 0 <= d <= 7]
    fixed = [r for r in rows if fix_date_in_period(r, f)]
    on_time = [r for r in fixed if fixed_on_time(r)]
    late = [r for r in fixed if r["deadline"] is not None and not fixed_on_time(r)]
    fix_durations = [d for r in fixed if (d := fix_days(r)) is not None]

    open_now_prev = [
        r for r in prev_rows
        if is_open(r) and (r["closed_at"] is None or r["closed_at"] > f.bounds[1])
    ]
    cur = len(open_rows)
    prev = len(open_now_prev)
    delta_pct = round((cur - prev) / prev * 100, 1) if prev else None

    return {
        "open": cur,
        "overdue": len(overdue),
        "due_7": len(due7),
        "fixed": len(fixed),
        "on_time_pct": round(len(on_time) / len(fixed) * 100, 1) if fixed else None,
        "avg_fix_days": round(sum(fix_durations) / len(fix_durations), 1) if fix_durations else None,
        "delta_open_pct": delta_pct,
        "drill": {
            "late_ids": [r["id"] for r in late],
        },
    }


def compute_category_kpis(rows: list[dict], grade_by_cat: dict, today: date) -> dict:
    open_rows = [r for r in rows if is_open(r)]
    a_rows = [r for r in open_rows if grade_by_cat.get(r["category_id"]) == "a"]
    a_overdue = [r for r in a_rows if is_overdue(r, today)]
    return {
        "critical_open": len(a_rows),
        "critical_overdue": len(a_overdue),
        "critical_objects": len({r["object_id"] for r in a_rows}),
    }


def object_ranking(rows: list[dict], grade_by_cat: dict, today: date) -> list[dict]:
    by_obj: dict[str, dict] = {}
    for r in rows:
        item = by_obj.setdefault(
            r["object_name"],
            {"object": r["object_name"], "object_id": r["object_id"],
             "open": 0, "overdue": 0, "critical": 0,
             "fixed": 0, "on_time": 0},
        )
        if is_open(r):
            item["open"] += 1
            if is_overdue(r, today):
                item["overdue"] += 1
            if grade_by_cat.get(r["category_id"]) == "a":
                item["critical"] += 1
        elif r["status"] == "closed" and closed_date(r) is not None:
            item["fixed"] += 1
            if fixed_on_time(r):
                item["on_time"] += 1
    result = sorted(
        by_obj.values(),
        key=lambda i: (-(i["overdue"] + i["critical"]), -i["open"]),
    )
    for i in result:
        i["on_time_pct"] = round(i["on_time"] / i["fixed"] * 100, 1) if i["fixed"] else None
    return result


def structure(rows: list[dict], key: str, id_key: str) -> list[list]:
    counter: dict[str, list] = {}
    for r in rows:
        if is_open(r) or r["status"] == "closed":
            name = r.get(key) or "—"
            item = counter.setdefault(name, [name, 0, r.get(id_key)])
            item[1] += 1
            if item[2] is None:
                item[2] = r.get(id_key)
    return sorted(counter.values(), key=lambda kv: kv[1], reverse=True)


def dynamics(rows: list[dict], f: Filters) -> dict:
    start, end = f.bounds
    span = (end - start).days
    if span <= 92:
        step, gran = timedelta(days=7), "week"
    elif span <= 1200:
        step, gran = timedelta(days=30), "month"
    else:
        # Сжатие длинных периодов, чтобы число корзин оставалось читаемым.
        step_days = max(30, span // 40)
        step, gran = timedelta(days=step_days), "month"
    buckets: list[dict] = []
    cur = start
    while cur <= end:
        nxt = min(cur + step - timedelta(days=1), end)
        issued = 0
        fixed = 0
        for r in rows:
            d = r["issue_date"] or r["doc_date"]
            if d is not None and cur <= d <= nxt:
                issued += 1
            cd = closed_date(r)
            if cd is not None and cur <= cd <= nxt:
                fixed += 1
        buckets.append({
            "label": cur.strftime("%d.%m"),
            "issued": issued,
            "fixed": fixed,
        })
        cur = nxt + timedelta(days=1)
    running = 0
    for b in buckets:
        running += b["issued"] - b["fixed"]
        b["open_balance"] = running
    return {"granularity": gran, "buckets": buckets}


def need_decision(rows: list[dict], grade_by_cat: dict, today: date, limit: int = 5) -> list[dict]:
    out: list[dict] = []
    seen: set[int] = set()

    def add(r: dict, reason: str):
        if r["id"] not in seen and is_open(r):
            seen.add(r["id"])
            out.append({"id": r["id"], "doc_number": r["doc_number"],
                        "object": r["object_name"], "reason": reason})

    for r in rows:
        grade = grade_by_cat.get(r["category_id"])
        od = days_overdue(r, today)
        if grade == "a" and od > 14:
            add(r, f"Категория А просрочена на {od} дн.")
    for r in rows:
        if grade_by_cat.get(r["category_id"]) == "a" and (
            r["responsible_id"] is None or r["deadline"] is None
        ):
            add(r, "Категория А: нет ответственного или срока")
    for r in rows:
        if r["recurrence_of_id"] is not None:
            add(r, "Повторное замечание после устранения")
    for r in rows:
        if not is_open(r):
            continue
        last = r["updated_at"] or r["issue_date"] or r["doc_date"]
        if last and grade_by_cat.get(r["category_id"]) in ("a", "b"):
            if (today - last).days > 10:
                add(r, f"Без движения {(today - last).days} дн.")
    return out[:limit]


# --- сборка экрана -----------------------------------------------------------

PROPAGATED_FILTERS = (
    "object_id", "section_id", "contractor_id", "mgmt_id",
    "category_id", "kind_id", "work_type_id", "responsible_id",
)


def make_href_builders(f: Filters, start: date, end: date):
    """Фабрики drill-URL: когорта по дате выдачи и по дате устранения."""
    prop = "&".join(
        f"{k}={f.raw[k]}" for k in PROPAGATED_FILTERS
        if f.raw.get(k) not in (None, "", "none")
    )

    def cohort_href(extra: str) -> str:
        qs = f"issued_from={start.isoformat()}&issued_to={end.isoformat()}&include_drafts=0"
        if prop:
            qs += "&" + prop
        if extra:
            qs += "&" + extra
        return "/prescriptions?" + qs

    def closed_href(extra: str) -> str:
        qs = (f"closed_from={start.isoformat()}&closed_to={end.isoformat()}"
              f"&status=closed&include_drafts=0")
        if prop:
            qs += "&" + prop
        if extra:
            qs += "&" + extra
        return "/prescriptions?" + qs

    return cohort_href, closed_href


def traffic_light(rows: list[dict], cat: dict, today: date,
                  cohort_href, closed_href) -> list[dict]:
    open_rows = [r for r in rows if is_open(r)]
    due7 = [r for r in open_rows if (d := due_in_days(r, today)) is not None and 0 <= d <= 7]
    overdue = [r for r in open_rows if is_overdue(r, today)]
    fixed = [r for r in rows if r["status"] == "closed" and closed_date(r) is not None]
    return [
        {"label": "Критические (категория А)", "value": cat["critical_open"], "color": "red",
         "href": cohort_href("critical=1&status=open")},
        {"label": "Просроченные", "value": len(overdue), "color": "red",
         "href": cohort_href("overdue=1&status=open")},
        {"label": "Срок в ближайшие 7 дней", "value": len(due7), "color": "orange",
         "href": cohort_href("due_within=7&status=open")},
        {"label": "Устранено за период", "value": len(fixed), "color": "green",
         "href": closed_href("")},
    ]


def build_executive(db: Session, params: dict) -> dict:
    today = date.today()
    f = parse_filters(params)
    weights = get_risk_weights(db)
    grade_ids = category_id_map(db)
    grade_by_cat = {cid: grade for grade, cid in grade_ids.items()}

    rows = scope_rows(db, f)
    start, end = f.bounds
    span = (end - start).days + 1
    prev_f = parse_filters(params)
    prev_f.date_from, prev_f.date_to = start - timedelta(days=span), start - timedelta(days=1)
    prev_rows = scope_rows(db, prev_f)

    kpis = compute_kpis(rows, f, prev_rows, today)
    cat_kpis = compute_category_kpis(rows, grade_by_cat, today)

    scored = sorted(
        ((risk_score(r, weights, grade_by_cat, today), r) for r in rows if is_open(r)),
        key=lambda t: (-t[0], t[1]["doc_date"]),
    )
    top10 = [
        {
            "score": s,
            "band": risk_band(s),
            "id": r["id"],
            "doc_number": r["doc_number"],
            "prescription_type": r["prescription_type"],
            "object": r["object_name"],
            "section": r["section_name"],
            "work": r["work_name"],
            "category": r["category_name"],
            "issued": (r["issue_date"] or r["doc_date"]).isoformat() if (r["issue_date"] or r["doc_date"]) else None,
            "deadline": r["deadline"].isoformat() if r["deadline"] else None,
            "overdue_days": days_overdue(r, today),
            "responsible": r["responsible_name"],
            "contractor": r["contractor_name"],
            "summary": r["description"][:80],
        }
        for s, r in scored[:10]
    ]

    cohort_href, closed_href = make_href_builders(f, start, end)

    def pct_color(value, good=80, ok=60):
        if value is None:
            return "muted"
        return "green" if value >= good else ("warn" if value >= ok else "red")

    delta = kpis["delta_open_pct"]
    kpi_cards = [
        {"key": "open", "label": "Открытые предписания",
         "value": kpis["open"], "unit": "шт", "color": "navy",
         "href": cohort_href("status=open"),
         "formula": "Открытые (новое/на проверке/в работе/заявлено), выданные в периоде; черновики исключены"},
        {"key": "critical_open", "label": "Критические, категория А",
         "value": cat_kpis["critical_open"], "unit": "шт", "color": "red",
         "href": cohort_href("status=open&critical=1"),
         "formula": "Из открытых — с категорией А"},
        {"key": "overdue", "label": "Просроченные",
         "value": kpis["overdue"], "unit": "шт",
         "color": "red" if kpis["overdue"] else "navy",
         "href": cohort_href("status=open&overdue=1"),
         "formula": "Открытые с плановым сроком ранее сегодняшней даты"},
        {"key": "due_7", "label": "Срок в ближайшие 7 дней",
         "value": kpis["due_7"], "unit": "шт", "color": "warn",
         "href": cohort_href("status=open&due_within=7"),
         "formula": "Открытые с 0 ≤ срок − сегодня ≤ 7"},
        {"key": "fixed", "label": "Устранено за период",
         "value": kpis["fixed"], "unit": "шт", "color": "green",
         "href": closed_href(""),
         "formula": "Закрытые с датой устранения в периоде"},
        {"key": "on_time_pct", "label": "Доля устранения в срок",
         "value": kpis["on_time_pct"], "unit": "%", "color": pct_color(kpis["on_time_pct"]),
         "href": closed_href("ontime=late"),
         "formula": "В срок / устранено за период × 100; клик — нарушившие срок"},
        {"key": "avg_fix_days", "label": "Средний срок устранения",
         "value": kpis["avg_fix_days"], "unit": "дн", "color": "navy",
         "href": closed_href(""),
         "formula": "Среднее (устранение − выдача) по устранённым за период"},
        {"key": "delta_open_pct", "label": "Δ открытых к прошлому периоду",
         "value": delta, "unit": "%",
         "color": "red" if (delta or 0) > 0 else "green",
         "href": cohort_href("status=open"),
         "formula": "(открытые сейчас − открытые на конец прошлого периода) / прошлый × 100"},
    ]

    a_id = grade_ids.get("a")
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "period": {"from": start.isoformat(), "to": end.isoformat(),
                   "granularity": "week" if span <= 92 else "month"},
        "filters": {k: v for k, v in f.raw.items() if v},
        "kpis": {**kpis, **cat_kpis, "drill": kpis["drill"]},
        "kpi_cards": kpi_cards,
        "traffic_light": traffic_light(rows, cat_kpis, today, cohort_href, closed_href),
        "objects": object_ranking(rows, grade_by_cat, today),
        "structure_category": structure(rows, "category_name", "category_id"),
        "structure_work": structure(rows, "work_name", "work_type_id")[:12],
        "dynamics": dynamics(rows, f),
        "need_decision": need_decision(rows, grade_by_cat, today),
        "top10": top10,
        "critical_category_id": a_id,
        "rows_total": len(rows),
    }
