from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

SAFE_EXTRA_FIELDS = (
    "payment_id",
    "attempt",
    "error_category",
    "error_type",
    "outbox_id",
    "event_type",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        for field in SAFE_EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str) -> None:
    logger = logging.getLogger("app")
    if not any(getattr(handler, "_payment_service_handler", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler._payment_service_handler = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
