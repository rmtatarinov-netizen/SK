"""Локальный запуск веб-интерфейса (Шаг 9).

    python scripts/run_web.py

Хост/порт — из config.yaml (web.host/web.port). Только для локальной сети;
в продакшене — за reverse proxy с TLS.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402

from src.config import get_settings  # noqa: E402
from src.web.app import app  # noqa: E402


def main() -> int:
    settings = get_settings()
    if settings.web_secret_key == "change-me-in-env":
        print(
            "ВНИМАНИЕ: WEB_SECRET_KEY не задан в .env — сессии небезопасны.",
            file=sys.stderr,
        )
    uvicorn.run(
        app,
        host=settings.web.host,
        port=settings.web.port,
        log_level=settings.logging.level.lower(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
