"""
uitp_api.logging_config
=======================
Structured logging setup. Text for local dev; JSON for production.
SECURITY: never pass SecretStr values through log formatters.
"""
from __future__ import annotations

import logging
import sys
from typing import Any


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _JsonLineFormatter() if fmt == "json"
        else logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logging.basicConfig(level=level.upper(), handlers=[handler])
    if level.upper() != "DEBUG":
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


class _JsonLineFormatter(logging.Formatter):
    """One-JSON-object-per-line formatter for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        import json
        payload: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)
