"""Detailed local diagnostics; never attach these values to telemetry spans/metrics."""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar

from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)
_context = ContextVar("local_diagnostic_context", default=None)
_secret_fields = {
    "authorization",
    "api_key",
    "password",
    "secret",
    "access_token",
    "refresh_token",
    "client_secret",
    "private_key",
    "id_token",
    "cookie",
    "set_cookie",
}


@contextmanager
def diagnostic_scope(**values):
    token = _context.set({**(_context.get() or {}), **values})
    try:
        yield
    finally:
        _context.reset(token)


def redacted_json(values):
    def clean(value):
        if isinstance(value, dict):
            return {
                str(k): "[redacted]"
                if str(k).lower().replace("-", "_") in _secret_fields
                else clean(v)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        return value

    data = json.dumps(clean(values), default=str, ensure_ascii=False)
    # Credentials can appear inside provider error strings or nested JSON strings.
    for name, secret in os.environ.items():
        if name.endswith(("_KEY", "_TOKEN", "_PASSWORD", "_SECRET")) and len(secret) >= 6:
            data = data.replace(secret, "[redacted]")
            data = data.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[redacted]")
    data = re.sub(r"(?i)Bearer\s+[a-z0-9._~+/-]+=*", "Bearer [redacted]", data)
    return data


def diagnostic(event, **values):
    if os.getenv("LLM_DIAGNOSTICS_ENABLED", "true").lower() in {"0", "false", "off", "no"}:
        return
    data = redacted_json({**(_context.get() or {}), **values})
    limit = max(2000, int(os.getenv("LLM_DIAGNOSTIC_MAX_CHARS", "60000")))
    if len(data) > limit:
        data = data[:limit] + f" [truncated; original_chars={len(data)}]"
    logger.info("[diagnostics] %s %s", event, data)
