"""Запуск Telegram-бота (Шаг 10).

    python scripts/run_bot.py                 # long polling до Ctrl+C
    python scripts/run_bot.py --cycles 1      # один цикл (проверка связности)

Требуется TELEGRAM_BOT_TOKEN в .env и запущенный Ollama не нужен —
бот работает с локальной БД.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.bots.telegram_bot import run_polling  # noqa: E402
from src.observability import configure_logging  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram bot worker")
    parser.add_argument("--cycles", type=int, default=None, help="остановить после N циклов опроса")
    args = parser.parse_args()

    configure_logging("telegram_bot")
    try:
        processed = run_polling(stop_after_cycles=args.cycles)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.cycles is not None:
        print(f"processed updates: {processed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
