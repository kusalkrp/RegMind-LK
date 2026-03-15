"""
Structured JSON logging configuration.
Call configure_json_logging() once at app startup (in main.py lifespan).
All modules use logging.getLogger(__name__) — no changes needed in them.

Standard fields on every record (Section 9.1 of design doc):
  timestamp, service, level, event, trace_id, duration_ms, tool_name, error

Never logs: GEMINI_API_KEY, POSTGRES_URL, passwords, JWT tokens, user PII.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any


_REDACTED_KEYS = frozenset({
    "gemini_api_key", "postgres_url", "password", "token",
    "secret", "api_key", "authorization", "cookie",
})


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record to stdout."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "service": "sltda-mcp",
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }

        # Pull structured extras attached by log_tool_call()
        for field in ("trace_id", "tool_name", "duration_ms", "error"):
            if hasattr(record, field):
                entry[field] = getattr(record, field)

        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str)


def configure_json_logging(level: str = "INFO") -> None:
    """
    Replace the root handler with a JSON formatter.
    Called once in main.py lifespan before the server accepts traffic.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove any existing handlers (e.g. basicConfig default)
    root.handlers.clear()

    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)

    logging.getLogger(__name__).info(
        "JSON logging configured", extra={"event": "logging_init"}
    )


def log_tool_call(
    logger: logging.Logger,
    tool_name: str,
    trace_id: str,
    duration_ms: float,
    status: str,
    error: str | None = None,
) -> None:
    """
    Emit a structured tool-call log record.
    Used by MCP tool wrappers to record every invocation.
    """
    extra = {
        "trace_id": trace_id,
        "tool_name": tool_name,
        "duration_ms": round(duration_ms, 2),
        "error": error,
    }
    if error:
        logger.error("tool_call status=%s", status, extra=extra)
    else:
        logger.info("tool_call status=%s", status, extra=extra)
