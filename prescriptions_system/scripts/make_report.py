"""Формирование отчёта из БД (Шаг 11).

Примеры:
    python scripts/make_report.py --format json --out reports/dashboard.json
    python scripts/make_report.py --format csv  --out reports/registry.csv
    python scripts/make_report.py --format xlsx --out reports/registry.xlsx
    python scripts/make_report.py --format html --out reports/report.html
    python scripts/make_report.py --format pdf  --out reports/report.pdf
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.engine import get_session_factory  # noqa: E402
from src.reports import generator  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Report generator (DB only)")
    parser.add_argument("--format", choices=["json", "csv", "xlsx", "html", "pdf"], default="json")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        if args.format == "json":
            data = generator.build_dashboard_data(db)
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        elif args.format == "csv":
            generator.export_csv(db, args.out)
        elif args.format == "xlsx":
            generator.export_xlsx(db, args.out)
        elif args.format == "html":
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(generator.render_report_html(db), encoding="utf-8")
        else:
            try:
                generator.render_report_pdf(db, args.out)
            except generator.PdfRenderUnavailable as exc:
                print(f"PDF недоступен: {exc}", file=sys.stderr)
                fallback = str(args.out)[:-4] + ".html"
                Path(fallback).write_text(generator.render_report_html(db), encoding="utf-8")
                print(f"Сохранен HTML-аналог: {fallback}")
                return 1
    finally:
        db.close()
    print(f"Отчёт ({args.format}): {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
