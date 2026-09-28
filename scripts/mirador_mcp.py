#!/usr/bin/env python3
"""Read-only stdio MCP bridge for querying Mirador traces from Codex."""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = "https://api.mirador.org"
TRACE_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
RELATIVE_TIME_RE = re.compile(r"^\d{1,4}(?:s|m|h|d|w)$")

TOOLS = [
    {
        "name": "mirador_identity",
        "description": "Show which Mirador organization and project the configured server key accesses.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "mirador_list_traces",
        "description": "Search recent Mirador traces. The default window is bounded to 24 hours; filters use Mirador's AIP-160 trace-filter syntax.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "Relative duration such as 24h or an RFC3339 timestamp."},
                "until": {"type": "string", "description": "Optional RFC3339 timestamp or relative duration."},
                "filter": {"type": "string", "description": "Optional Mirador trace filter, e.g. attribute.service.name=\"digital-brain-orchestrator\"."},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                "page_token": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "mirador_get_trace",
        "description": "Read one Mirador trace summary by its 32-character trace ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"trace_id": {"type": "string", "pattern": "^[0-9a-fA-F]{32}$"}},
            "required": ["trace_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "mirador_get_trace_events",
        "description": "Read the ordered event timeline for one Mirador trace.",
        "inputSchema": {
            "type": "object",
            "properties": {"trace_id": {"type": "string", "pattern": "^[0-9a-fA-F]{32}$"}},
            "required": ["trace_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "mirador_trace_attributes",
        "description": "Discover Mirador trace attribute keys and sampled values in a bounded time window.",
        "inputSchema": {
            "type": "object",
            "properties": {"since": {"type": "string", "description": "Relative duration such as 24h or an RFC3339 timestamp."}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]


def _bounded_since(value: Any) -> str:
    since = str(value or "24h")
    if len(since) > 64 or not (RELATIVE_TIME_RE.fullmatch(since) or since.endswith("Z")):
        raise ValueError("since must be a relative duration (e.g. 24h) or RFC3339 UTC timestamp")
    return since


def _api_get(path: str, query: dict[str, Any] | None = None) -> Any:
    key = os.getenv("MIRADOR_API_KEY", "").strip()
    if not key:
        raise RuntimeError("MIRADOR_API_KEY is not available to the MCP process")
    url = f"{API_BASE}{path}"
    if query:
        url += "?" + urlencode({k: v for k, v in query.items() if v is not None})
    request = Request(
        url,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        # Do not echo response bodies or credentials to the model or process logs.
        raise RuntimeError(f"Mirador API returned HTTP {exc.code} for {path}") from None
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not reach Mirador API ({type(exc).__name__})") from None


def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == "mirador_identity":
        return _api_get("/v1/identity")
    if name == "mirador_list_traces":
        since = _bounded_since(arguments.get("since"))
        page_size = int(arguments.get("page_size", 50))
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        trace_filter = str(arguments.get("filter", ""))
        if len(trace_filter) > 1000:
            raise ValueError("filter must be at most 1000 characters")
        return _api_get(
            "/v1/traces",
            {
                "since": since,
                "until": arguments.get("until"),
                "filter": trace_filter or None,
                "page_size": page_size,
                "page_token": arguments.get("page_token"),
            },
        )
    if name in {"mirador_get_trace", "mirador_get_trace_events"}:
        trace_id = str(arguments.get("trace_id", ""))
        if not TRACE_ID_RE.fullmatch(trace_id):
            raise ValueError("trace_id must be 32 hexadecimal characters")
        suffix = "/events" if name.endswith("_events") else ""
        return _api_get(f"/v1/traces/{trace_id}{suffix}")
    if name == "mirador_trace_attributes":
        return _api_get("/v1/traces/attributes", {"since": _bounded_since(arguments.get("since"))})
    raise ValueError("Unknown Mirador tool")


def _reply(message: dict[str, Any]) -> dict[str, Any]:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "digital-brain-mirador-readonly", "version": "1.0.0"},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        try:
            result = _call_tool(str(params.get("name", "")), params.get("arguments") or {})
            content = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": content}], "isError": False},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            }
    if request_id is None:
        return {}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}


def main() -> None:
    for line in sys.stdin:
        try:
            message = json.loads(line)
            if message.get("method", "").startswith("notifications/"):
                continue
            response = _reply(message)
            if response:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception:
            # Protocol stdout must remain valid JSON-RPC; malformed input is ignored.
            continue


if __name__ == "__main__":
    main()
