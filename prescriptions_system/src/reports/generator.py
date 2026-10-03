"""Сборка отчётов (Шаг 11): JSON для дашборда, CSV/XLSX-экспорт, HTML/PDF.

PDF рендерится WeasyPrint, если в системе доступны GTK-библиотеки;
иначе бросается PdfRenderUnavailable — всегда доступен print-friendly HTML
(печать из браузера), данные от этого не меняются.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from src.reports import queries

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"

CSV_COLUMNS = [
    "id", "doc_number", "doc_date", "type", "object", "contractor", "status",
    "deadline", "overdue", "requires_review", "confirmed_at", "closed_at",
    "created_at",
]


class PdfRenderUnavailable(RuntimeError):
    pass


def build_dashboard_data(db: Session) -> dict:
    """Единый JSON дашборда (структура из ТЗ §28) + метрики."""
    statuses = queries.status_breakdown(db)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": queries.total_prescriptions(db),
        "types": queries.type_breakdown(db),
        "statuses": statuses,
        "overdue": queries.overdue_count(db),
        "average_fix_days": queries.average_fix_days(db),
        "average_review_days": queries.average_review_days(db),
        "rework_returns": queries.rework_count(db),
        "by_contractor": queries.by_contractor(db),
        "by_object": queries.by_object(db),
        "norm_frequency": queries.norm_frequency(db),
        "notifications": queries.notification_stats(db),
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
    summary.append(["Средний срок устранения, дн.", data["average_fix_days"]])
    summary.append(["Среднее время до проверки, дн.", data["average_review_days"]])
    summary.append(["Возвратов на доработку", data["rework_returns"]])
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
