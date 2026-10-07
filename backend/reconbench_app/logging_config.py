"""Structured JSON logging with context propagation and recursive secret redaction."""

import logging
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

import structlog

_SENSITIVE_NAME = re.compile(
    r"(?:pass(?:word)?|secret|token|authorization|api.?key|access.?key|dsn)", re.I
)
_REDACTED = "[REDACTED]"
# Google API keys and key-bearing query strings, wherever they appear in a value.
_SENSITIVE_VALUE = re.compile(r"AIza[0-9A-Za-z_\-]{30,}|(?<=[?&]key=)[^&\s\"']+")


def redact_sensitive_fields(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Redact values whose field names can contain credentials, including nested data."""

    del logger, method_name

    def redact(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: _REDACTED if _SENSITIVE_NAME.search(str(key)) else redact(item)
                for key, item in value.items()
            }
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [redact(item) for item in value]
        if isinstance(value, str):
            return _SENSITIVE_VALUE.sub(_REDACTED, value)
        return value

    return redact(event_dict)


def configure_logging(level: str) -> None:
    """Configure stdlib and structlog once for newline-delimited JSON logs."""

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive_fields,
    ]
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper(), force=True)
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
