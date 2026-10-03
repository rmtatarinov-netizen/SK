"""Структурированное логирование (Шаг 12).

Единый JSON-форматтер для всех пайплайнов и сервисов; формат (JSON/plain) и
уровень — из config.yaml (logging.level, logging.json_logs).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
                timespec="seconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = str(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(service: str | None = None) -> None:
    """Настроить root-логгер один раз на процесс (идемпотентно)."""
    from src.config import get_settings

    settings = get_settings()
    root = logging.getLogger()
    if getattr(root, "_ps_configured", False):
        return
    level = getattr(logging, settings.logging.level.upper(), logging.INFO)
    root.setLevel(level)
    handler = logging.StreamHandler()
    if settings.logging.json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    root.handlers = [handler]
    root._ps_configured = True  # type: ignore[attr-defined]
    if service:
        logging.getLogger(service).info("logging configured", extra={"service": service})
