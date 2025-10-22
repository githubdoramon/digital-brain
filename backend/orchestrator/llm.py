from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Set

import requests

import retrieval
import sql_tools

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5:32b-instruct")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))

print(f"Using Ollama model: {OLLAMA_CHAT_MODEL}")

_SCHEMA_HINT_CACHE: str | None = None
_SCHEMA_SNAPSHOT: Dict[str, Any] | None = None


TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "describe_schema",
            "description": "Return available database tables and their columns.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_query",
            "description": "Parse natural language to identify relevant contacts, places, and time spans.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Natural language text to resolve. Defaults to the current question if omitted.",
                    },
                    "need_contacts": {
                        "type": "boolean",
                        "description": "Set false to skip contact resolution.",
                        "default": True,
                    },
                    "need_places": {
                        "type": "boolean",
                        "description": "Set false to skip place resolution.",
                        "default": True,
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memories",
            "description": "Search the memory store for events matching the query and optional filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Query string for semantic and keyword search. Defaults to the current question.",
                    },
                    "people": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter results to these contact IDs.",
                    },
                    "place_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter results to these place IDs.",
                    },
                    "time_start": {
                        "type": "string",
                        "description": "Inclusive ISO-8601 timestamp for the start of the window.",
                    },
                    "time_end": {
                        "type": "string",
                        "description": "Inclusive ISO-8601 timestamp for the end of the window.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "Maximum number of results to return.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_events",
            "description": "Fetch full event details, including people and places, for given event IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Event IDs to fetch.",
                    }
                },
                "required": ["ids"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_sql",
            "description": "Execute a read-only SQL query against the memories database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SQL query to run. Must be a single SELECT/CTE statement.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                        "description": "Maximum number of rows to return (default 200).",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
]


class AgentState:
    def __init__(self) -> None:
        self.resolution: Dict[str, Any] = {}
        self.search_results: List[Dict[str, Any]] = []
        self.detailed_events: List[Dict[str, Any]] = []

    def update_resolution(self, data: Dict[str, Any]) -> None:
        if isinstance(data, dict):
            self.resolution = data

    def update_search_results(self, data: Dict[str, Any]) -> None:
        if isinstance(data, dict):
            results = data.get("results")
            if isinstance(results, list):
                self.search_results = results
            else:
                self.search_results = []

    def update_detailed_events(self, events: List[Dict[str, Any]]) -> None:
        if isinstance(events, list):
            self.detailed_events = events
        else:
            self.detailed_events = []


def answer_question(question: str, search_limit: int = 3) -> Dict[str, Any]:
    state = AgentState()
    messages: List[Dict[str, Any]] = _build_messages(question, search_limit)

    while True:
        print("[agent] sending messages ->", json.dumps(messages[-4:], ensure_ascii=False, indent=2))
        response = _ollama_chat(messages)
        message = response.get("message") or {}
        print("[agent] received message ->", json.dumps(message, ensure_ascii=False, indent=2))
        if not message:
            raise RuntimeError(f"Unexpected Ollama response: {response}")

        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            call_names = [call.get("function", {}).get("name") for call in tool_calls]
            print(f"[agent] tool calls requested: {call_names}")
            messages.append(message)
            for call in tool_calls:
                tool_result = _handle_tool_call(call, state, question, search_limit)
                print(
                    f"[agent] tool result for {call.get('id')}: {json.dumps(tool_result, ensure_ascii=False)[:500]}"
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    }
                )
            continue

        content = (message.get("content") or "").strip()
        if not content:
            raise RuntimeError(f"Unexpected Ollama response: {response}")

        messages.append(message)
        bundle = _finalize_bundle(question, content, state, search_limit)
        print("[agent] final bundle ->", json.dumps(bundle, ensure_ascii=False, indent=2))
        return bundle


def _build_messages(question: str, search_limit: int) -> List[Dict[str, str]]:
    system_prompt = (
        "You are a personal memory assistant. "
        "Use the available tools to gather accurate context before answering. "
        "Do not fabricate events; if no relevant memories exist, say so. "
        f"When searching, prefer returning at most {search_limit} highly relevant results, unless the user is requesting full data."
    )
    protocol_prompt = "Tool protocol: when a question references stored memories, contacts, places, timelines, or relationships, " \
        "first refresh the schema with describe_schema, then plan any execute_sql queries needed to retrieve facts. " \
        "Cross-check every table or column in a planned SQL statement against the schema snapshot; never invent new tables. " \
        "Use resolve_query when entity or time extraction helps craft structured constraints. " \
        "For relationship closeness questions, first review `contacts.relationship` values (family > friend > coworker > other). " \
        "Do not stop after describing a plan—actually call the necessary tools, inspect their outputs, and base your final answer on that evidence. " \
        "If a SQL attempt fails validation, revise and retry until you either succeed or can explain why the data cannot be retrieved. " \
        "Only respond after you have gathered sufficient evidence from the tools, and cite how you derived the answer. " \
        "If you are not 100% sure, still respond to the original question, but state your uncertainty instead of declining outright."

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]

    schema_hint = _load_schema_hint()
    if schema_hint:
        messages.append({"role": "system", "content": schema_hint})

    messages.append({"role": "system", "content": protocol_prompt})
    messages.append({"role": "user", "content": question.strip()})
    return messages


def _ollama_chat(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload = {
        "model": OLLAMA_CHAT_MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "stream": False,
    }
    resp = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _handle_tool_call(
    tool_call: Dict[str, Any],
    state: AgentState,
    question: str,
    search_limit: int,
) -> Dict[str, Any]:
    function = tool_call.get("function") or {}
    name = function.get("name")
    raw_args = function.get("arguments") or "{}"

    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid arguments for tool {name}: {raw_args}") from exc

    if name == "resolve_query":
        text = args.get("text") or question
        need_contacts = _coerce_bool(args.get("need_contacts", True))
        need_places = _coerce_bool(args.get("need_places", True))
        print(f"[agent] calling resolve_query(text={text!r}, need_contacts={need_contacts}, need_places={need_places})")
        result = retrieval.resolve_query(text, need_contacts=need_contacts, need_places=need_places)
        state.update_resolution(result)
        return result

    if name == "search_memories":
        query = args.get("query") or question
        limit_arg = args.get("limit")
        try:
            limit = int(limit_arg) if limit_arg is not None else search_limit
        except (TypeError, ValueError):
            limit = search_limit
        limit = max(1, min(limit, search_limit))
        print(
            f"[agent] calling search_memories(query={query!r}, people={args.get('people')}, place_ids={args.get('place_ids')}, time_start={args.get('time_start')}, time_end={args.get('time_end')}, limit={limit})"
        )
        result = retrieval.search_memories(
            query=query,
            people=args.get("people"),
            place_ids=args.get("place_ids"),
            time_start=args.get("time_start"),
            time_end=args.get("time_end"),
            limit=limit,
        )
        state.update_search_results(result)
        return result

    if name == "get_events":
        ids = args.get("ids")
        if not isinstance(ids, list):
            raise RuntimeError("get_events requires an array of ids")
        print(f"[agent] calling get_events(ids={ids})")
        events = retrieval.get_events([str(i) for i in ids])
        state.update_detailed_events(events)
        return {"events": events}

    if name == "describe_schema":
        print("[agent] calling describe_schema()")
        return sql_tools.describe_schema()

    if name == "execute_sql":
        query = args.get("query")
        if not isinstance(query, str):
            raise RuntimeError("execute_sql requires a string query")
        limit_arg = args.get("limit")
        try:
            limit = int(limit_arg) if limit_arg is not None else 200
        except (TypeError, ValueError):
            limit = 200
        unknown_tables = _find_unknown_tables(query)
        if unknown_tables:
            print(f"[agent] rejecting execute_sql due to unknown tables: {unknown_tables}")
            return {
                "error": {
                    "message": "Unknown tables referenced in SQL.",
                    "unknown_tables": sorted(unknown_tables),
                    "available_tables": sorted(_get_schema_snapshot().keys()),
                }
            }
        print(f"[agent] calling execute_sql(limit={limit}) -> {query!r}")
        result = sql_tools.execute_sql(query, limit=limit)
        return _normalize_sql_result(result)

    raise RuntimeError(f"Unsupported tool requested: {name}")


def _load_schema_hint() -> str:
    global _SCHEMA_HINT_CACHE
    if _SCHEMA_HINT_CACHE is not None:
        return _SCHEMA_HINT_CACHE

    snapshot = _get_schema_snapshot()
    if not snapshot:
        _SCHEMA_HINT_CACHE = ""
        return ""

    try:
        relationship_examples = _fetch_relationship_examples()
    except Exception:
        relationship_examples = []

    lines: List[str] = [
        "Database schema snapshot (read-only):",
    ]
    for table_name in sorted(snapshot):
        columns = snapshot[table_name].get("columns") or []
        column_bits: List[str] = []
        for col in columns:
            name = col.get("name")
            dtype = col.get("type")
            if name and dtype:
                column_bits.append(f"{name} ({dtype})")
            elif name:
                column_bits.append(name)
        if len(column_bits) > 6:
            summary = ", ".join(column_bits[:6]) + ", ..."
        else:
            summary = ", ".join(column_bits)
        lines.append(f"- {table_name}: {summary}")
        if table_name == "contacts" and relationship_examples:
            lines.append(
                "  Distinct relationships observed: "
                + ", ".join(sorted(relationship_examples))
            )
    lines.append("Use describe_schema for full details and execute_sql to pull rows.")

    hint = "\n".join(lines)
    _SCHEMA_HINT_CACHE = hint
    return hint


def _get_schema_snapshot() -> Dict[str, Any]:
    global _SCHEMA_SNAPSHOT
    if _SCHEMA_SNAPSHOT is not None:
        return _SCHEMA_SNAPSHOT

    try:
        schema_snapshot = sql_tools.describe_schema()
    except Exception:
        _SCHEMA_SNAPSHOT = {}
        return {}

    tables = schema_snapshot.get("tables") or {}
    # Normalize keys to their original case but allow later lookups to lowercase
    _SCHEMA_SNAPSHOT = tables
    return tables


def _find_unknown_tables(query: str) -> Set[str]:
    snapshot = _get_schema_snapshot()
    if not snapshot:
        return set()
    known_tables = {name.lower() for name in snapshot.keys()}
    referenced = _extract_table_names(query)
    return {table for table in referenced if table not in known_tables}


_TABLE_REF_REGEX = re.compile(r"\b(?:from|join|into)\s+([a-zA-Z_][\w.]*)", re.IGNORECASE)


def _extract_table_names(query: str) -> Set[str]:
    tables: Set[str] = set()
    if not query:
        return tables
    for match in _TABLE_REF_REGEX.findall(query):
        candidate = match.strip()
        if not candidate:
            continue
        # Strip aliasing or quoting remnants
        candidate = candidate.strip('"')
        if " " in candidate:
            candidate = candidate.split(" ")[0]
        if "," in candidate:
            candidate = candidate.split(",")[0]
        if "." in candidate:
            candidate = candidate.split(".")[-1]
        lowered = candidate.lower()
        if lowered and lowered not in {"select", "from", "join", "unnest"}:
            tables.add(lowered)
    return tables


def _fetch_relationship_examples() -> List[str]:
    try:
        from db import get_conn
    except Exception:
        return []

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT relationship
            FROM contacts
            WHERE relationship IS NOT NULL
            ORDER BY relationship
            LIMIT 12
            """
        )
        rows = cur.fetchall()
    return [row[0] for row in rows if row and row[0]]


def _finalize_bundle(
    question: str,
    answer: str,
    state: AgentState,
    search_limit: int,
) -> Dict[str, Any]:
    if not state.resolution or not state.search_results:
        fallback = retrieval.run_pipeline(question, search_limit=search_limit)
        if not state.resolution:
            state.update_resolution(fallback.get("resolution") or {})
        if not state.search_results:
            state.update_search_results({"results": fallback.get("search_results", [])})
        if not state.detailed_events:
            state.update_detailed_events(fallback.get("detailed_events", []))
    elif state.search_results and not state.detailed_events:
        ids = [row.get("id") for row in state.search_results if row.get("id")]
        if ids:
            events = retrieval.get_events(ids)
            state.update_detailed_events(events)

    return {
        "question": question,
        "answer": answer,
        "resolution": state.resolution or {},
        "search_results": state.search_results,
        "detailed_events": state.detailed_events,
    }


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return bool(value)


def _normalize_sql_result(result: Dict[str, Any]) -> Dict[str, Any]:
    rows = result.get("rows")
    if isinstance(rows, list):
        normalized = []
        for row in rows:
            if isinstance(row, dict):
                normalized.append(_json_safe(row))
            else:
                normalized.append(row)
        result = dict(result)
        result["rows"] = normalized
    return result


def _json_safe(row: Dict[str, Any]) -> Dict[str, Any]:
    safe: Dict[str, Any] = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            safe[key] = value.isoformat()
        else:
            safe[key] = value
    return safe
