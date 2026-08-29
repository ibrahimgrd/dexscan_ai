"""Structured logging setup.

Playbook reference: Unified Developer Playbook, Part VIII Step 1;
standard described in Part V.4.

Every log line carries timestamp, level, and module name, as real JSON
(built with `json.dumps`, not string-templated - a log message containing
a `"` character would otherwise silently corrupt the JSON output). Log-level
conventions for later steps: DEBUG for engine-internal detail, INFO for
state transitions and completed scans, WARNING for degraded-engine
fallbacks, ERROR for anything surfaced to the user as a failure.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

# Fields every stdlib LogRecord carries - used to detect caller-supplied
# extra={...} fields so they can be folded into the JSON payload without
# hardcoding their names.
_RESERVED_RECORD_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys())


class _JSONFormatter(logging.Formatter):
    """Emits one JSON object per log record: timestamp, level, module,
    message, plus any extra={...} fields the caller attached."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_RECORD_FIELDS
        }
        if extras:
            payload["extra"] = extras
        return json.dumps(payload, default=str)


def configure(log_level: str = "INFO") -> None:
    """Configure the root logger once, at process startup, with a
    structured JSON formatter writing to stdout. Never use print() anywhere
    else in this codebase (Part V.4). Unknown level names fall back to INFO
    rather than raising, since a bad .env value shouldn't crash startup."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JSONFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
