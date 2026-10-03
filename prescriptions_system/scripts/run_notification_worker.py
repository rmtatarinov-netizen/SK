"""Запуск воркера доставки уведомлений (Шаг 8; лог — Шаг 12).

Один цикл:      python scripts/run_notification_worker.py --once
Постоянный:     python scripts/run_notification_worker.py --interval 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.engine import get_session_factory  # noqa: E402
from src.notifications.worker import (  # noqa: E402
    process_pending_notifications,
    run_forever,
)
from src.observability import configure_logging  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Notification delivery worker")
    parser.add_argument("--once", action="store_true", help="один цикл и выход")
    parser.add_argument("--interval", type=float, default=5.0, help="пауза, сек")
    args = parser.parse_args()

    configure_logging("notification_worker")

    if args.once:
        db = get_session_factory()()
        try:
            counters = process_pending_notifications(db)
            print(json.dumps(counters, ensure_ascii=False))
        finally:
            db.close()
        return 0

    run_forever(poll_interval_s=args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
