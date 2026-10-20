"""Shared request policy and bounded provider diagnostics, without source text."""

from __future__ import annotations

import json
import os
import re
import threading
import time

import requests

_CONSTRAINTS = {}
_LOCK = threading.Lock()


def _key(model):
    return os.getenv("LLM_BASE_URL", ""), model


def remember_constraint(model, error):
    with _LOCK:
        if len(_CONSTRAINTS) >= 64:
            _CONSTRAINTS.clear()
        row = _CONSTRAINTS.setdefault(_key(model), {})
        row["expires"] = time.monotonic() + 3600
        if error.code == "output_limit_exceeded" and error.limit:
            row["output_limit"] = error.limit
        if error.code == "unsupported_response_format":
            row["json_object"] = True


def apply_constraints(payload):
    with _LOCK:
        row = _CONSTRAINTS.get(_key(payload.get("model")), {})
        row = dict(row) if row.get("expires", 0) > time.monotonic() else {}
    limits = [
        n
        for n in (int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "0")), row.get("output_limit", 0))
        if n > 0
    ]
    if limits and payload.get("max_tokens"):
        payload["max_tokens"] = min(payload["max_tokens"], *limits)
    if (
        row.get("json_object")
        and (payload.get("response_format") or {}).get("type") == "json_schema"
    ):
        payload["response_format"] = {"type": "json_object"}


def request_timeout(payload, timeout=None):
    base = timeout or int(os.getenv("LLM_TIMEOUT", "180"))
    reasoning = payload.get("reasoning")
    effort = payload.get("reasoning_effort") or (
        reasoning.get("effort") if isinstance(reasoning, dict) else None
    )
    if effort in {"high", "xhigh", "max"}:
        return max(base, int(os.getenv("LLM_HIGH_REASONING_TIMEOUT", "900")))
    if effort == "medium":
        return max(base, int(os.getenv("LLM_REASONING_TIMEOUT", "300")))
    return base


class LLMRequestRejected(requests.HTTPError):
    """Only static reason codes and bounded numeric limits escape the provider body."""

    def __init__(self, code, *, response=None, limit=None):
        super().__init__(code, response=response)
        self.code = code
        self.limit = limit


def request_rejection(response):
    # Providers sometimes put echoed input in error.message. Inspect a bounded
    # prefix locally, but never return/log arbitrary message, code or param values.
    body = str(getattr(response, "text", "") or "")[:16000].lower()
    code, limit = "invalid_request", None
    if getattr(response, "status_code", None) == 413:
        return LLMRequestRejected("context_length_exceeded", response=response)
    if any(
        s in body
        for s in (
            "context_length_exceeded",
            "maximum context length",
            "maximum input",
            "input is too long",
            "prompt is too long",
        )
    ):
        code = "context_length_exceeded"
    elif any(
        s in body for s in ("max_tokens", "max_completion_tokens", "output must be between")
    ) and any(s in body for s in ("maximum", "must be", "at most", "exceed", "less than")):
        code = "output_limit_exceeded"
        match = re.search(
            r"(?:between\s+\d+\s+and|at most|maximum(?:\s+(?:is|of))?)\s*(\d{2,7})\s*(?:tokens)?",
            body,
        )
        if match and 64 <= int(match[1]) <= 1000000:
            limit = int(match[1])
    elif any(s in body for s in ("response_format", "json_schema", "structured output")) and any(
        s in body for s in ("unsupported", "not supported", "does not support")
    ):
        code = "unsupported_response_format"
    elif any(s in body for s in ("invalid_json_schema", "invalid schema")):
        code = "invalid_response_schema"
    return LLMRequestRejected(code, response=response, limit=limit)


def failure_code(error):
    """Stable diagnostics for background work; exception strings can contain evidence."""
    if isinstance(error, LLMRequestRejected):
        return error.code
    if isinstance(error, requests.Timeout):
        return "model_timeout"
    if isinstance(error, requests.ConnectionError):
        return "model_unavailable"
    if isinstance(error, json.JSONDecodeError):
        return "invalid_json"
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        if status in {400, 413, 422}:
            return request_rejection(response).code
        if status in {401, 403}:
            return "model_authentication"
        if status == 429:
            return "model_rate_limited"
        if status >= 500:
            return "model_unavailable"
    # Imported lazily to keep this module usable by llm_helpers itself.
    from llm_helpers import LLMUnavailableError

    if isinstance(error, LLMUnavailableError):
        return "model_unavailable"
    return "analysis_failed"
