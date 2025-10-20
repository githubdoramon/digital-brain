from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import requests

import retrieval
import sql_tools

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "llama3.1:8b-instruct-q8_0")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))

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
        print("[agent] sending messages ->", json.dumps(messages[-2:], ensure_ascii=False, indent=2))
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
        f"When searching, prefer returning at most {search_limit} highly relevant results."
    )
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": question.strip(),
        },
    ]


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
        print(f"[agent] calling execute_sql(limit={limit}) -> {query!r}")
        result = sql_tools.execute_sql(query, limit=limit)
        return _normalize_sql_result(result)

    raise RuntimeError(f"Unsupported tool requested: {name}")


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
