"""Сборка отчётов (Шаг 11): JSON для дашборда, CSV/XLSX-экспорт, HTML/PDF.

PDF рендерится WeasyPrint, если в системе доступны GTK-библиотеки;
иначе бросается PdfRenderUnavailable — всегда доступен print-friendly HTML
(печать из браузера), данные от этого не меняются.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from src.db.models import PrescriptionStatus
from src.reports import queries

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"

CSV_COLUMNS = [
    "id", "doc_number", "doc_date", "type", "object", "contractor", "status",
    "deadline", "overdue", "requires_review", "confirmed_at", "closed_at",
    "created_at",
]


class PdfRenderUnavailable(RuntimeError):
    pass


def get_period_dates(period_type: str | None, custom_start: date | None = None, custom_end: date | None = None) -> tuple[date | None, date | None]:
    """Вычисляет границы периода строительства (скользящее окно)."""
    if not period_type or period_type == "custom":
        return custom_start, custom_end

    # Окончание окна — вторник текущей недели (день приёмки/среза)
    today = date.today()
    current_tuesday = today - timedelta(days=(today.weekday() - 1) % 7)

    if period_type == "week":
        delta = timedelta(days=7)
    elif period_type == "month":
        delta = timedelta(days=30)
    elif period_type == "year":
        delta = timedelta(days=365)
    else:
        return None, None

    return current_tuesday - delta, current_tuesday


def build_dashboard_data(db: Session, filters: dict = None) -> dict:
    """Единый JSON дашборда (структура из ТЗ §28) + метрики."""
    filters = filters or {}
    
    # Расчет дат периода
    date_from, date_to = get_period_dates(
        filters.get("period_type"),
        filters.get("date_from"),
        filters.get("date_to")
    )
    
    # Формируем итоговый фильтр для запросов
    query_filters = {
        "enterprise_id": filters.get("enterprise_id"),
        "prescription_type": filters.get("prescription_type"),
        "date_from": date_from,
        "date_to": date_to,
    }
    
    # Сбор основных данных
    total = queries.total_prescriptions(db, filters=query_filters)
    fixed = len(
        [
            p
            for p in queries.get_filtered_prescriptions(db, **query_filters)
            if p.status == PrescriptionStatus.CLOSED
        ]
    )

    overdue = queries.overdue_count(db, filters=query_filters)
    expired = queries.deadline_expired_count(db, filters=query_filters)
    not_fixed = total - fixed

    avg_fix = queries.average_fix_days(db, filters=query_filters)
    avg_review = queries.average_review_days(db, filters=query_filters)
    rework = queries.rework_count(db, filters=query_filters)
    completion_pct = round((fixed / total * 100), 1) if total else 0

    statuses = queries.status_breakdown(db, filters=query_filters)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": total,
        "fixed": fixed,
        "not_fixed": not_fixed,
        "overdue": overdue,
        "deadline_expired": expired,
        "average_fix_days": avg_fix,
        "average_review_days": avg_review,
        "rework_returns": rework,
        "completion_pct": completion_pct,
        "types": queries.type_breakdown(db, filters=query_filters),
        "statuses": statuses,
        "by_contractor": queries.by_contractor(db, filters=query_filters),
        "by_object": queries.by_object(db, filters=query_filters),
        "norm_frequency": queries.norm_frequency(db),
        "notifications": queries.notification_stats(db, filters=query_filters),
    }


def export_csv(db: Session, path_or_buffer=None) -> str:
    rows = queries.registry_rows(db)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    text = buffer.getvalue()
    if path_or_buffer is not None:
        target = Path(path_or_buffer)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8-sig")
    return text


def export_xlsx(db: Session, path) -> Path:
    from openpyxl import Workbook

    wb = Workbook()
    sheet = wb.active
    sheet.title = "Реестр"
    sheet.append(CSV_COLUMNS)
    for row in queries.registry_rows(db):
        sheet.append([row[column] for column in CSV_COLUMNS])

    summary = wb.create_sheet("Сводка")
    data = build_dashboard_data(db)
    summary.append(["Метрика", "Значение"])
    summary.append(["Всего предписаний", data["total"]])
    summary.append(["Заказчика", data["types"].get("customer", 0)])
    summary.append(["Внутренних", data["types"].get("internal", 0)])
    for status, count in data["statuses"].items():
        summary.append([f"Статус: {status}", count])
    summary.append(["Просрочено", data["overdue"]])
    summary.append(["Не устранено", data["not_fixed"]])
    summary.append(["Средний срок устранения, дн.", data["average_fix_days"]])
    summary.append(["Среднее время проверки, дн.", data["average_review_days"]])
    summary.append(["Возвратов на доработку", data["rework_returns"]])
    summary.append(["Процент выполнения", data["completion_pct"]])
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    wb.save(target)
    return target


def _jinja_env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)


def render_report_html(db: Session) -> str:
    data = build_dashboard_data(db)
    template = _jinja_env().get_template("reports/report_pdf.html")
    return template.render(data=data)


def render_report_pdf(db: Session, path) -> Path:
    """PDF через WeasyPrint; при недоступности GTK — PdfRenderUnavailable."""
    html = render_report_html(db)
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as exc:  # ImportError или OSError без GTK на Windows
        raise PdfRenderUnavailable(
            "WeasyPrint недоступен в этой системе (нужны GTK-библиотеки). "
            "Используйте print-friendly HTML (/reports/print) или браузерную печать."
        ) from exc
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        HTML(string=html).write_pdf(str(target))
    except Exception as exc:  # OSError при загрузке библиотек
        raise PdfRenderUnavailable(
            "WeasyPrint не может отрендерить PDF в этой системе "
            "(ошибка графических библиотек). Используйте HTML-печать."
        ) from exc
    return target
