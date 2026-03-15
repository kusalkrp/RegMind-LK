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

# Standard LogRecord attribute names — never treated as "extra" fields
_STANDARD_RECORD_KEYS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
    # Internal formatter attributes
    "getMessage",
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

        # Pull the four named structured extras set by log_tool_call()
        for field in ("trace_id", "tool_name", "duration_ms", "error"):
            if hasattr(record, field):
                entry[field] = getattr(record, field)

        # Pull any additional extra fields, redacting sensitive ones
        for key, val in record.__dict__.items():
            if key in _STANDARD_RECORD_KEYS or key.startswith("_"):
                continue
            if key in entry:  # already handled above
                continue
            # Redact any key whose name contains a sensitive substring
            if any(redact_word in key.lower() for redact_word in _REDACTED_KEYS):
                entry[key] = "[REDACTED]"
            else:
                entry[key] = val

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


def log_cost_estimate(
    logger: logging.Logger,
    tool_name: str,
    embedding_calls: int,
    synthesis_calls: int,
    embedding_tokens_estimate: int = 500,   # rough: avg short query
    synthesis_tokens_estimate: int = 2000,  # rough: context + response
) -> None:
    """
    Emit a structured cost-estimate log record.
    Uses rough token estimates; not a billing-accurate figure.
    Rates: $0.00001/embedding token, $0.000001/synthesis token.
    Never raises — failure is silently swallowed.
    """
    try:
        embedding_cost = embedding_calls * embedding_tokens_estimate * 0.00001
        synthesis_cost = synthesis_calls * synthesis_tokens_estimate * 0.000001
        total_cost = embedding_cost + synthesis_cost
        logger.info(
            "cost_estimate",
            extra={
                "tool_name": tool_name,
                "cost_usd_estimate": round(total_cost, 8),
                "embedding_calls": embedding_calls,
                "synthesis_calls": synthesis_calls,
            },
        )
    except Exception as exc:
        logger.warning("log_cost_estimate failed (non-critical): %s", exc)
