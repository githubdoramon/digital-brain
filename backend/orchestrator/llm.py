from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict, List, Optional

import requests

import contacts as contacts_service
import conversations
import documents as documents_service
import events as events_service
import retrieval
import sql_tools
import web_tools
import tags_manager
import skills

OLLAMA_HOST = os.getenv("OLLAMA_HOST")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))  # Keep default for timeout

EVENT_PROPOSAL_START = "<event_proposal>"
EVENT_PROPOSAL_END = "</event_proposal>"

# Validate required configuration
if not OLLAMA_HOST:
    raise RuntimeError("OLLAMA_HOST environment variable is required")
if not OLLAMA_CHAT_MODEL:
    raise RuntimeError("OLLAMA_CHAT_MODEL environment variable is required")
if not OLLAMA_EMBED_MODEL:
    raise RuntimeError("OLLAMA_EMBED_MODEL environment variable is required")

def _log_timing(label: str, start_time: float, **metadata: Any) -> None:
    elapsed = perf_counter() - start_time
    meta_items = []
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, float):
            meta_items.append(f"{key}={value:.3f}")
        else:
            meta_items.append(f"{key}={value}")
    meta_str = " ".join(meta_items)
    if meta_str:
        print(f"[timing] {label} took {elapsed:.3f}s ({elapsed*1000:.0f}ms) {meta_str}")
    else:
        print(f"[timing] {label} took {elapsed:.3f}s ({elapsed*1000:.0f}ms)")


TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "describe_schema",
            "description": "Return available database tables and their columns so you have full context of what you can search in the database.",
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
            "description": "Search the memory store for events and documents matching the query and optional filters.",
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
            "name": "get_document",
            "description": "Retrieve a single document, either by ID or by title (fuzzy match), including full content and metadata.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Document ID to fetch.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Title to match when the exact document ID is unknown.",
                    },
                },
                "oneOf": [{"required": ["id"]}, {"required": ["title"]}],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_sql",
            "description": "Execute a read-only SQL query against the memories database, containing events, contacts, places, documents, and other information relevant to the user.",
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
    {
        "type": "function",
        "function": {
            "name": "internet_search",
            "description": "Search the public internet for up-to-date information beyond the internal memory store.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Web search query string. Defaults to the current question if omitted.",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Maximum number of web results to return (default configured via environment).",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_web_page",
            "description": "Fetch the contents of a specific web page using Tavily's crawler for deeper context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Absolute HTTP or HTTPS URL to fetch. Required.",
                    },
                    "include_links": {
                        "type": "boolean",
                        "description": "Set true to include page hyperlinks in the response (defaults to environment configuration).",
                    },
                    "include_images": {
                        "type": "boolean",
                        "description": "Set true to include image metadata in the response (defaults to environment configuration).",
                    },
                    "include_raw_html": {
                        "type": "boolean",
                        "description": "Set true to include raw HTML when supported (defaults to environment configuration).",
                    },
                    "max_characters": {
                        "type": "integer",
                        "minimum": 1000,
                        "maximum": 100000,
                        "description": "Optional cap on extracted text length (defaults to environment configuration).",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_skill_script",
            "description": "Execute a script from an active skill. Only use when a skill with scripts is active and instructs you to run a specific script.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "Name of the active skill containing the script.",
                    },
                    "script_name": {
                        "type": "string",
                        "description": "Name of the script file to execute (e.g., 'generate.py').",
                    },
                    "args": {
                        "type": "object",
                        "description": "Arguments to pass to the script as JSON.",
                    },
                },
                "required": ["skill_name", "script_name"],
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
        self.web_results: List[Dict[str, Any]] = []
        self.web_summary: Optional[str] = None
        self.web_follow_up_questions: List[str] = []
        self.web_query: Optional[str] = None
        self.web_provider: Optional[str] = None
        self.web_response_id: Optional[str] = None
        self.web_documents: List[Dict[str, Any]] = []
        # Skills tracking
        self.activated_skills: List[Dict[str, Any]] = []

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

    def update_web_context(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return

        results = data.get("results")
        if isinstance(results, list):
            self.web_results = [r for r in results if isinstance(r, dict)]
        else:
            self.web_results = []

        summary = data.get("summary")
        self.web_summary = summary if isinstance(summary, str) else None

        follow_ups = data.get("follow_up_questions")
        if isinstance(follow_ups, list):
            self.web_follow_up_questions = [str(item) for item in follow_ups if isinstance(item, (str, int, float))]
        else:
            self.web_follow_up_questions = []

        query = data.get("query")
        self.web_query = query if isinstance(query, str) else None

        provider = data.get("provider")
        self.web_provider = provider if isinstance(provider, str) else None

        response_id = data.get("response_id")
        self.web_response_id = response_id if isinstance(response_id, str) else None

    def update_web_documents(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return

        new_documents: List[Dict[str, Any]] = []

        documents = data.get("documents")
        if isinstance(documents, list):
            new_documents.extend([doc for doc in documents if isinstance(doc, dict)])

        single_document = data.get("document")
        if isinstance(single_document, dict):
            new_documents.append(single_document)

        if new_documents:
            self.web_documents.extend(new_documents)

        summary = data.get("summary")
        if isinstance(summary, str) and summary.strip():
            self.web_summary = summary.strip()

        provider = data.get("provider")
        if isinstance(provider, str):
            self.web_provider = provider

        response_id = data.get("response_id")
        if isinstance(response_id, str):
            self.web_response_id = response_id


async def answer_question(
    question: str,
    search_limit: int = 3,
    user_id: str = "default_user",
    session_id: Optional[str] = None,
    user_email: Optional[str] = None,
    event_capture_enabled: bool = False,
) -> Dict[str, Any]:
    total_start = perf_counter()
    current_utc = datetime.now(timezone.utc)
    local_now = current_utc.astimezone()
    time_context = (
        "Current time context available to you:\n"
        f"- UTC now: {current_utc.isoformat()}\n"
        f"- Local system time: {local_now.isoformat()}"
    )

    state = AgentState()

    # Get conversation history from persistent storage
    conversation_history: List[Dict[str, str]] = []
    if session_id and user_email:
        history_start = perf_counter()
        try:
            conversation_history = conversations.get_conversation_history(session_id, user_email)
            _log_timing(
                "session.load_history",
                history_start,
                session_id=session_id,
                messages=len(conversation_history),
            )
            print(f"[session] Retrieved {len(conversation_history)} messages from session {session_id}")
        except Exception as exc:
            _log_timing("session.load_history.error", history_start, session_id=session_id)
            print(f"[session] Failed to load history for session {session_id}: {exc}")
            conversation_history = []

    build_start = perf_counter()
    messages: List[Dict[str, Any]] = _build_messages(
        question,
        search_limit,
        conversation_history,
        user_email=user_id,
        current_time_context=time_context,
        event_capture_enabled=event_capture_enabled,
        state=state,
    )
    _log_timing("agent.build_messages", build_start, message_count=len(messages))

    iteration = 0
    thinking_retries = 0

    while True:
        iteration += 1
        loop_start = perf_counter()
        print(f"[agent] iteration {iteration} start")
        print("[agent] sending messages ->", json.dumps(messages, ensure_ascii=False, indent=2, default=str))
        chat_start = perf_counter()
        response = _ollama_chat(messages)
        _log_timing("ollama.chat", chat_start, iteration=iteration)
        message = response.get("message") or {}
        print("[agent] received message ->", json.dumps(message, ensure_ascii=False, indent=2, default=str))
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
                    f"[agent] tool result for {call.get('id')}: {json.dumps(tool_result, ensure_ascii=False, default=str)[:500]}"
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": json.dumps(tool_result, ensure_ascii=False, default=str),
                    }
                )
            _log_timing(
                "agent.iteration",
                loop_start,
                iteration=iteration,
                event="tool_calls",
                tools=len(tool_calls),
            )
            continue

        content = (message.get("content") or "").strip()
        thinking = (message.get("thinking") or "").strip()
        if not content:
            if thinking:
                thinking_retries += 1

                if thinking_retries > 3:
                    raise RuntimeError(
                        "Assistant returned internal reasoning multiple times without a final answer."
                    )

                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Reminder: provide the final answer for the user in natural language without"
                            " exposing internal reasoning."
                        ),
                    }
                )
                continue

            raise RuntimeError(f"Unexpected Ollama response: {response}")

        thinking_retries = 0

        messages.append(message)

        event_proposal = _extract_event_proposal(content) if event_capture_enabled else None

        # Update session store with this Q&A pair
        new_thread_title: Optional[str] = None
        if session_id and user_email:
            try:
                assistant_metadata = {"event_proposal": event_proposal} if event_proposal else {}
                persist_result = conversations.record_exchange(
                    thread_id=session_id,
                    user_email=user_email,
                    user_message=question,
                    assistant_message=content,
                    user_metadata={},
                    assistant_metadata=assistant_metadata,
                )
                print(f"[session] Persisted exchange in session {session_id}")
                if (
                    persist_result.get("message_count_before", 0) == 0
                    and conversations.is_default_title(persist_result.get("previous_title"))
                ):
                    generated_title = _generate_thread_title_from_prompt(question)
                    if generated_title:
                        updated = conversations.update_thread_title(session_id, user_email, generated_title)
                        if updated:
                            new_thread_title = updated.get("title")
                            print(f"[session] Updated thread title for {session_id}: {new_thread_title!r}")
            except Exception as exc:
                print(f"[session] Failed to persist exchange for session {session_id}: {exc}")
        bundle = _finalize_bundle(
            question,
            content,
            state,
            search_limit,
            session_id,
            event_proposal=event_proposal,
        )
        if new_thread_title:
            bundle["thread_title"] = new_thread_title

        answer_length = len(content)
        search_results_count = len(state.search_results)
        _log_timing(
            "agent.iteration",
            loop_start,
            iteration=iteration,
            event="final",
            answer_chars=answer_length,
            search_results=search_results_count,
        )

        print("[agent] final bundle ->", json.dumps(bundle, ensure_ascii=False, indent=2, default=str))
        _log_timing(
            "answer_question.total",
            total_start,
            iterations=iteration,
            session_id=session_id,
            user_id=user_id,
        )
        return bundle


# ----------------------- Streaming Implementation -----------------------

from typing import AsyncGenerator

async def _ollama_chat_stream(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Stream responses from Ollama, yielding chunks as they arrive.

    Uses httpx for async HTTP streaming.
    """
    import httpx

    payload = {
        "model": OLLAMA_CHAT_MODEL,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "stream": True,
    }

    timeout = httpx.Timeout(OLLAMA_TIMEOUT, connect=10.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            f"{OLLAMA_HOST}/api/chat",
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.strip():
                    try:
                        chunk = json.loads(line)
                        yield chunk
                        if chunk.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue


import asyncio

async def _run_skill_script_with_queue(
    tool_call: Dict[str, Any],
    state: AgentState,
    status_queue: asyncio.Queue,
) -> Dict[str, Any]:
    """
    Run a skill script, pushing status messages to the queue in real-time.

    Returns the final result dict.
    """
    function = tool_call.get("function") or {}
    raw_args = function.get("arguments") or "{}"

    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid arguments for run_skill_script: {raw_args}") from exc

    skill_name = args.get("skill_name")
    script_name = args.get("script_name")
    script_args = args.get("args") or {}

    if not skill_name or not isinstance(skill_name, str):
        return {"error": "run_skill_script requires a skill_name string"}
    if not script_name or not isinstance(script_name, str):
        return {"error": "run_skill_script requires a script_name string"}

    # Verify the skill is active
    active_skill_names = [s.get("name") for s in state.activated_skills]
    if skill_name not in active_skill_names:
        return {
            "error": f"Skill '{skill_name}' is not active. Active skills: {active_skill_names}"
        }

    # Get skill and run script with streaming
    registry = skills.get_registry()
    skill = registry.get_skill(skill_name)
    if not skill:
        return {"error": f"Skill '{skill_name}' not found in registry"}

    print(f"[agent] calling run_skill_script (streaming) skill={skill_name}, script={script_name}, args={script_args}")
    step_start = perf_counter()

    runner = skills.get_runner_for_skill(skill.path)

    # Async callback that pushes status to queue
    async def push_status(line: str) -> None:
        if line.startswith("STATUS: "):
            status_msg = line[8:]  # Strip "STATUS: " prefix
            await status_queue.put({"type": "status", "message": status_msg})

    # Use streaming execution with async callback
    result = await runner.run_script_streaming(
        script_name,
        script_args,
        on_output_async=push_status,
    )

    _log_timing(
        "tool.run_skill_script.streaming",
        step_start,
        skill=skill_name,
        script=script_name,
        returncode=result.get("returncode"),
    )
    return result


async def answer_question_stream(
    question: str,
    search_limit: int = 3,
    user_id: str = "default_user",
    session_id: Optional[str] = None,
    user_email: Optional[str] = None,
    event_capture_enabled: bool = False,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Stream LLM responses, yielding events as they occur.

    Event types:
    - {"type": "token", "content": "..."} - Text chunks as they arrive
    - {"type": "tool_call", "name": "...", "args": {...}} - Tool invocations
    - {"type": "tool_result", "name": "...", "result": {...}} - Tool outputs
    - {"type": "status", "message": "..."} - Status updates
    - {"type": "done", "bundle": {...}} - Final complete response

    Args:
        question: User's question
        search_limit: Max search results
        user_id: User identifier
        session_id: Conversation session ID
        user_email: User email for context
        event_capture_enabled: Whether to extract event proposals
    """
    total_start = perf_counter()
    current_utc = datetime.now(timezone.utc)
    local_now = current_utc.astimezone()
    time_context = (
        "Current time context available to you:\n"
        f"- UTC now: {current_utc.isoformat()}\n"
        f"- Local system time: {local_now.isoformat()}"
    )

    state = AgentState()

    # Get conversation history from persistent storage
    conversation_history: List[Dict[str, str]] = []
    if session_id and user_email:
        try:
            conversation_history = conversations.get_conversation_history(session_id, user_email)
            print(f"[session] Retrieved {len(conversation_history)} messages from session {session_id}")
        except Exception as exc:
            print(f"[session] Failed to load history for session {session_id}: {exc}")
            conversation_history = []

    messages: List[Dict[str, Any]] = _build_messages(
        question,
        search_limit,
        conversation_history,
        user_email=user_id,
        current_time_context=time_context,
        event_capture_enabled=event_capture_enabled,
        state=state,
    )

    iteration = 0
    accumulated_content = ""

    while True:
        iteration += 1
        yield {"type": "status", "message": f"Thinking (iteration {iteration})..."}

        tool_calls = []
        current_content = ""

        async for chunk in _ollama_chat_stream(messages, TOOLS):
            message = chunk.get("message", {})

            # Stream content tokens
            delta = message.get("content", "")
            if delta:
                current_content += delta
                yield {"type": "token", "content": delta}

            # Collect tool calls (may come in chunks)
            chunk_tool_calls = message.get("tool_calls")
            if chunk_tool_calls:
                tool_calls.extend(chunk_tool_calls)

            if chunk.get("done"):
                break

        # Handle tool calls
        if tool_calls:
            messages.append({
                "role": "assistant",
                "content": current_content,
                "tool_calls": tool_calls,
            })

            for call in tool_calls:
                func = call.get("function", {})
                func_name = func.get("name", "unknown")
                func_args = func.get("arguments", {})

                yield {
                    "type": "tool_call",
                    "name": func_name,
                    "args": func_args if isinstance(func_args, dict) else {},
                }

                # Use streaming execution for skill scripts
                if func_name == "run_skill_script":
                    # Create queue for real-time status updates
                    status_queue: asyncio.Queue = asyncio.Queue()

                    # Run script in background task
                    script_task = asyncio.create_task(
                        _run_skill_script_with_queue(call, state, status_queue)
                    )

                    # Yield status messages as they arrive
                    while not script_task.done():
                        try:
                            # Wait for status with short timeout
                            event = await asyncio.wait_for(
                                status_queue.get(), timeout=0.1
                            )
                            yield event
                        except asyncio.TimeoutError:
                            continue

                    # Get final result
                    result = await script_task

                    # Drain any remaining status messages
                    while not status_queue.empty():
                        event = await status_queue.get()
                        yield event
                else:
                    # Use sync handler for all other tools
                    result = _handle_tool_call(call, state, question, search_limit)

                yield {
                    "type": "tool_result",
                    "name": func_name,
                    "result": result,
                }

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })

            continue

        # No tool calls - we have the final answer
        accumulated_content = current_content
        break

    # Extract event proposal if enabled
    event_proposal = _extract_event_proposal(accumulated_content) if event_capture_enabled else None

    # Persist conversation
    new_thread_title: Optional[str] = None
    if session_id and user_email:
        try:
            assistant_metadata = {"event_proposal": event_proposal} if event_proposal else {}
            persist_result = conversations.record_exchange(
                thread_id=session_id,
                user_email=user_email,
                user_message=question,
                assistant_message=accumulated_content,
                user_metadata={},
                assistant_metadata=assistant_metadata,
            )
            print(f"[session] Persisted exchange in session {session_id}")

            if (
                persist_result.get("message_count_before", 0) == 0
                and conversations.is_default_title(persist_result.get("previous_title"))
            ):
                generated_title = _generate_thread_title_from_prompt(question)
                if generated_title:
                    updated = conversations.update_thread_title(session_id, user_email, generated_title)
                    if updated:
                        new_thread_title = updated.get("title")
                        print(f"[session] Updated thread title for {session_id}: {new_thread_title!r}")
        except Exception as exc:
            print(f"[session] Failed to persist exchange for session {session_id}: {exc}")

    # Build final bundle
    bundle = _finalize_bundle(
        question,
        accumulated_content,
        state,
        search_limit,
        session_id,
        event_proposal=event_proposal,
    )
    if new_thread_title:
        bundle["thread_title"] = new_thread_title

    _log_timing(
        "answer_question_stream.total",
        total_start,
        iterations=iteration,
        session_id=session_id,
        user_id=user_id,
    )

    yield {"type": "done", "bundle": bundle}


def _generate_thread_title_from_prompt(question: str) -> Optional[str]:
    cleaned_question = (question or "").strip()
    if not cleaned_question:
        return None

    prompt = (
        "Create a concise, engaging title for the user's question. "
        "Limit to at most 6 words, use Title Case, and avoid ending punctuation. "
        "Respond with the title only.\n\n"
        f"Question: {cleaned_question}"
    )
    payload = {
        "model": OLLAMA_CHAT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You generate short conversation titles. Keep them under 6 words and descriptive.",
            },
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }

    try:
        response = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        message = data.get("message") or {}
        candidate = (message.get("content") or "").strip()
        if not candidate:
            return None
    except Exception as exc:
        print(f"[session] Failed to generate thread title via LLM: {exc}")
        candidate = cleaned_question

    candidate = candidate.splitlines()[0].strip(' "\'')
    return candidate or None


def _build_messages(
    question: str,
    search_limit: int,
    conversation_history: List[Dict[str, str]] = None,
    user_email: Optional[str] = None,
    current_time_context: Optional[str] = None,
    event_capture_enabled: bool = False,
    state: Optional[AgentState] = None,
) -> List[Dict[str, str]]:
    system_prompt = (
        "You are a personal memory assistant helping the user explore their stored memories, contacts, events, and documents. "
        "Be conversational and helpful - make the user feel like they're talking to a knowledgeable friend, not a robot. "
        "Never fabricate information; if no relevant memories exist, say so honestly. "
        "Never expose raw IDs (like contact:1761950388937) - always use human-readable names and titles. "
        f"Prefer returning at most {search_limit} highly relevant results unless the user requests more."
    )
    protocol_prompt = (
        "Tool usage guidelines:\n"
        "- Use `describe_schema` if you need to understand database structure before writing SQL.\n"
        "- Use `search_memories` for semantic/vector search across events and documents.\n"
        "- Use `execute_sql` for precise queries - validate column names against schema, use ILIKE for case-insensitive matching.\n"
        "- Use `resolve_query` to extract contacts, places, and time ranges from natural language.\n"
        "- The database is personal to this user - all data relates to them.\n"
        "- Tasks/todos are in the 'todos' table.\n\n"
        "Important behaviors:\n"
        "- Actually call tools and use their results - don't just describe what you would do.\n"
        "- If a SQL query fails, revise and retry based on the error.\n"
        "- Respond in the same language the user asked in.\n"
        "- If uncertain, provide your best answer with appropriate caveats rather than refusing.\n\n"
        "For detailed guidance on specific topics (contacts, events, documents), refer to any activated skills below."
    )

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]

    schema_hint = sql_tools.load_schema_hint()
    if schema_hint:
        messages.append({"role": "system", "content": schema_hint})

    tags_context = _tag_context_for_agent()
    if tags_context:
        messages.append({"role": "system", "content": tags_context})

    # Self context based on authenticated email
    if user_email:
        self_context = _self_context_from_email(user_email)
        if self_context:
            messages.append({"role": "system", "content": self_context})

    messages.append({"role": "system", "content": protocol_prompt})
    
    if current_time_context:
        messages.append({"role": "system", "content": current_time_context})

    if event_capture_enabled:
        messages.append({"role": "system", "content": _event_capture_prompt()})

    # Skills integration: inject skill index and matching skills
    try:
        registry = skills.get_registry()

        # Always include skill index (lightweight, ~50 tokens per skill)
        skill_index = registry.get_skill_index()
        if skill_index:
            messages.append({"role": "system", "content": skill_index})

        # Find and inject matching skills based on user question
        matching_skills = registry.find_matching_skills(question)
        for match in matching_skills:
            skill_prompt = (
                f"ACTIVE SKILL [{match.skill.name}] (confidence: {match.confidence:.2f}):\n"
                f"{match.skill.instructions}"
            )
            messages.append({"role": "system", "content": skill_prompt})
            print(f"[skills] Activated skill: {match.skill.name} (confidence: {match.confidence:.2f})")

            # Track activated skills in state
            if state:
                state.activated_skills.append({
                    "name": match.skill.name,
                    "confidence": match.confidence,
                    "has_scripts": match.skill.has_scripts,
                })
    except Exception as exc:
        print(f"[skills] Error loading skills: {exc}")

    # Add conversation history (short-term session memory)
    if conversation_history:
        for msg in conversation_history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        print(f"[session] Added {len(conversation_history)} messages from session history")

    messages.append({"role": "user", "content": question.strip()})
    return messages


def _event_capture_prompt() -> str:
    return (
        "The user is describing something that happened to them or someone they know. "
        "Your job is to extract a precise event record for storage. "
        "When you do not have high confidence about key facts (start time, participants, place, title), ask concise clarifying questions instead of inventing details. "
        f"When you are confident enough to propose an event, provide a brief summary in natural language and append a single JSON object enclosed between {EVENT_PROPOSAL_START} and {EVENT_PROPOSAL_END}. "
        "Do not wrap the JSON in code fences. The JSON must include: "
        '{"title": "...", '
        '"startDate": "ISO-8601 timestamp (required)", '
        '"endDate": "ISO-8601 timestamp or null", '
        '"summary": "...", '
        '"people": ["list of people involved (names or contact ids)"], '
        '"place": "text description of the location", '
        '"placeId": "existing place id if you know it, otherwise null", '
        '"tags": ["relevant tags"], '
        '"types": ["event types"], '
        '"confidence": number between 0 and 1, '
        '"missing": ["fields you are still uncertain about"]}. '
        "Only include the JSON when you are ready to propose an insertable event."
    )


def _self_context_from_email(email: str) -> Optional[str]:
    contact = contacts_service.get_contact_by_email(email)
    if not contact:
        return None

    display_name = contact.get("display_name") or email
    contact_id = contact.get("contact_id")
    details: List[str] = []

    aliases = contact.get("aliases") or []
    if aliases:
        details.append("Aliases: " + ", ".join(aliases))

    tags = contact.get("tags") or []
    if tags:
        details.append("Tags: " + ", ".join(tags))

    relationships = contact.get("relationships") or []
    if relationships:
        rel_bits = []
        for rel in relationships:
            other = rel.get("contact_id")
            rel_type = rel.get("type")
            direction = rel.get("direction")
            if other and rel_type:
                rel_bits.append(f"{rel_type} ({direction}) → {other}")
        if rel_bits:
            details.append("Relationships: " + "; ".join(rel_bits))

    base = [
        "You are assisting the authenticated user.",
        f"User email: {email}",
        f"Corresponding contact: {display_name} ({contact_id})" if contact_id else f"Corresponding contact: {display_name}",
    ]

    if details:
        base.extend(details)

    return "\n".join(base)


def _tag_context_for_agent() -> Optional[str]:
    major_tags = getattr(tags_manager, "MAJOR_TAGS", None)
    major_keywords = getattr(tags_manager, "MAJOR_TAG_KEYWORDS", None)
    if not major_tags:
        return None

    lines = [
        "Tag taxonomy available for events and documents. Use these when searching or proposing filters.",
        f"Major tags: {', '.join(major_tags)}.",
        "Keyword cues per major tag:",
    ]
    for tag in major_tags:
        keywords = (major_keywords or {}).get(tag) or []
        if keywords:
            lines.append(f"- {tag}: {', '.join(keywords)}")
    return "\n".join(lines)


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
        step_start = perf_counter()
        result = retrieval.resolve_query(text, need_contacts=need_contacts, need_places=need_places)
        contacts = len(result.get("contacts", [])) if isinstance(result, dict) else "n/a"
        places = len(result.get("places", [])) if isinstance(result, dict) else "n/a"
        _log_timing(
            "tool.resolve_query",
            step_start,
            need_contacts=need_contacts,
            need_places=need_places,
            contacts=contacts,
            places=places,
        )
        state.update_resolution(result)
        return result

    if name == "search_memories":
        query = args.get("query") or question
        limit_arg = args.get("limit")
        try:
            limit = int(limit_arg) if limit_arg is not None else search_limit
        except (TypeError, ValueError):
            limit = search_limit
        limit = max(1, min(5, min(limit, search_limit)))

        print(
            f"[agent] calling search_memories(query={query!r}, people={args.get('people')}, place_ids={args.get('place_ids')}, time_start={args.get('time_start')}, time_end={args.get('time_end')}, limit={limit})"
        )
        step_start = perf_counter()
        result = retrieval.search_memories(
            query=query,
            people=args.get("people"),
            place_ids=args.get("place_ids"),
            time_start=args.get("time_start"),
            time_end=args.get("time_end"),
            limit=limit,
        )
        results_list = result.get("results") if isinstance(result, dict) else None
        _log_timing(
            "tool.search_memories",
            step_start,
            limit=limit,
            results=len(results_list) if isinstance(results_list, list) else "n/a",
        )
        state.update_search_results(result)
        return result

    if name == "get_events":
        ids = args.get("ids")
        if not isinstance(ids, list):
            raise RuntimeError("get_events requires an array of ids")
        print(f"[agent] calling get_events(ids={ids})")
        step_start = perf_counter()
        events = events_service.get_events([str(i) for i in ids])
        _log_timing("tool.get_events", step_start, count=len(events))
        state.update_detailed_events(events)
        return {"events": events}

    if name == "get_document":
        document_id_raw = args.get("id")
        title_raw = args.get("title")
        document_id = document_id_raw.strip() if isinstance(document_id_raw, str) else None
        title = title_raw.strip() if isinstance(title_raw, str) else None

        if not document_id and not title:
            raise RuntimeError("get_document requires either id or title")

        step_start = perf_counter()

        if document_id:
            print(f"[agent] calling get_document(id={document_id})")
            document = documents_service.get_document(document_id)
            match_strategy = "id"
        else:
            print(f"[agent] searching document by title={title!r}")
            search_results = documents_service.search_documents(title, limit=1)
            top = search_results[0] if search_results else None
            matched_id = top.get("document_id") if isinstance(top, dict) else None
            document = documents_service.get_document(matched_id) if matched_id else None
            match_strategy = "title"

        content_len = len(document.get("content") or "") if isinstance(document, dict) else 0
        _log_timing(
            "tool.get_document",
            step_start,
            found=bool(document),
            content_chars=content_len,
            strategy=match_strategy,
        )
        if not document:
            return {
                "document": None,
                "error": "Document not found",
                "strategy": match_strategy,
                "title": title,
                "id": document_id,
            }
        return {"document": document, "strategy": match_strategy}

    if name == "describe_schema":
        print("[agent] calling describe_schema()")
        step_start = perf_counter()
        result = sql_tools.describe_schema()
        tables = result.get("tables") if isinstance(result, dict) else None
        table_count = len(tables) if isinstance(tables, dict) else "n/a"
        _log_timing("tool.describe_schema", step_start, tables=table_count)
        return result

    if name == "execute_sql":
        query = args.get("query")
        if not isinstance(query, str):
            raise RuntimeError("execute_sql requires a string query")
        limit_arg = args.get("limit")
        try:
            limit = int(limit_arg) if limit_arg is not None else 200
        except (TypeError, ValueError):
            limit = 200
        validation_start = perf_counter()
        unknown_tables = sql_tools.find_unknown_tables(query)
        _log_timing(
            "tool.execute_sql.validation",
            validation_start,
            unknown=len(unknown_tables),
            unknown_list=",".join(sorted(unknown_tables)) if unknown_tables else None,
        )
        if unknown_tables:
            print(f"[agent] rejecting execute_sql due to unknown tables: {unknown_tables}")
            return {
                "error": {
                    "message": "Unknown tables referenced in SQL.",
                    "unknown_tables": sorted(unknown_tables),
                    "available_tables": sorted(sql_tools.get_schema_snapshot().keys()),
                }
            }
        print(f"[agent] calling execute_sql(limit={limit}) -> {query!r}")
        step_start = perf_counter()
        result = sql_tools.execute_sql(query, limit=limit)
        normalized = _normalize_sql_result(result)
        rows = normalized.get("rows") if isinstance(normalized, dict) else None
        _log_timing(
            "tool.execute_sql",
            step_start,
            limit=limit,
            rows=len(rows) if isinstance(rows, list) else "n/a",
        )
        return normalized

    if name == "internet_search":
        raw_query = args.get("query")
        query = raw_query if isinstance(raw_query, str) and raw_query.strip() else question
        max_results_arg = args.get("max_results")
        try:
            max_results = int(max_results_arg) if max_results_arg is not None else None
        except (TypeError, ValueError):
            max_results = None
        print(f"[agent] calling internet_search(query={query!r}, max_results={max_results})")
        step_start = perf_counter()
        result = web_tools.internet_search(query=query, max_results=max_results)
        hits = result.get("results") if isinstance(result, dict) else None
        _log_timing(
            "tool.internet_search",
            step_start,
            max_results=max_results,
            results=len(hits) if isinstance(hits, list) else "n/a",
        )
        if not result.get("error"):
            state.update_web_context(result)
        return result

    if name == "fetch_web_page":
        raw_url = args.get("url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            raise RuntimeError("fetch_web_page requires a url string")

        include_links_arg = args.get("include_links")
        include_images_arg = args.get("include_images")
        include_raw_html_arg = args.get("include_raw_html")
        max_characters_arg = args.get("max_characters")

        include_links = None if include_links_arg is None else _coerce_bool(include_links_arg)
        include_images = None if include_images_arg is None else _coerce_bool(include_images_arg)
        include_raw_html = None if include_raw_html_arg is None else _coerce_bool(include_raw_html_arg)

        try:
            max_characters = int(max_characters_arg) if max_characters_arg is not None else None
        except (TypeError, ValueError):
            max_characters = None

        print(
            "[agent] calling fetch_web_page(url=%r, include_links=%s, include_images=%s, max_characters=%s)"
            % (raw_url.strip(), include_links, include_images, max_characters)
        )
        step_start = perf_counter()
        result = web_tools.fetch_web_page(
            raw_url,
            include_links=include_links,
            include_images=include_images,
            include_raw_html=include_raw_html,
            max_characters=max_characters,
        )
        documents = result.get("documents") if isinstance(result, dict) else None
        _log_timing(
            "tool.fetch_web_page",
            step_start,
            documents=len(documents) if isinstance(documents, list) else "n/a",
        )
        if not result.get("error"):
            state.update_web_documents(result)
        return result

    if name == "run_skill_script":
        skill_name = args.get("skill_name")
        script_name = args.get("script_name")
        script_args = args.get("args") or {}

        if not skill_name or not isinstance(skill_name, str):
            return {"error": "run_skill_script requires a skill_name string"}
        if not script_name or not isinstance(script_name, str):
            return {"error": "run_skill_script requires a script_name string"}

        # Verify the skill is active
        active_skill_names = [s.get("name") for s in state.activated_skills]
        if skill_name not in active_skill_names:
            return {
                "error": f"Skill '{skill_name}' is not active. Active skills: {active_skill_names}"
            }

        # Get skill and run script
        registry = skills.get_registry()
        skill = registry.get_skill(skill_name)
        if not skill:
            return {"error": f"Skill '{skill_name}' not found in registry"}

        print(f"[agent] calling run_skill_script(skill={skill_name}, script={script_name}, args={script_args})")
        step_start = perf_counter()

        runner = skills.get_runner_for_skill(skill.path)
        result = runner.run_script(script_name, script_args)

        _log_timing(
            "tool.run_skill_script",
            step_start,
            skill=skill_name,
            script=script_name,
            returncode=result.get("returncode"),
        )
        return result

    raise RuntimeError(f"Unsupported tool requested: {name}")


def _finalize_bundle(
    question: str,
    answer: str,
    state: AgentState,
    search_limit: int,
    session_id: Optional[str],
    event_proposal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not state.resolution or not state.search_results:
        fallback_start = perf_counter()
        fallback = retrieval.run_pipeline(question, search_limit=search_limit)
        fallback_results = fallback.get("search_results") if isinstance(fallback, dict) else None
        _log_timing(
            "pipeline.run_pipeline",
            fallback_start,
            search_results=len(fallback_results) if isinstance(fallback_results, list) else "n/a",
        )
        if not state.resolution:
            state.update_resolution(fallback.get("resolution") or {})
        if not state.search_results:
            state.update_search_results({"results": fallback.get("search_results", [])})
        if not state.detailed_events:
            state.update_detailed_events(fallback.get("detailed_events", []))
    elif state.search_results and not state.detailed_events:
        ids = [
            row.get("id")
            for row in state.search_results
            if row.get("id") and row.get("kind", "event") == "event"
        ]
        if ids:
            events_start = perf_counter()
            events = events_service.get_events(ids)
            _log_timing("pipeline.get_events", events_start, count=len(events))
            state.update_detailed_events(events)

    document_results = [
        row
        for row in state.search_results
        if isinstance(row, dict) and row.get("kind") == "document"
    ]

    return {
        "question": question,
        "answer": answer,
        "resolution": state.resolution or {},
        "search_results": state.search_results,
        "detailed_events": state.detailed_events,
        "document_results": document_results,
        "session_id": session_id,
        "thread_id": session_id,
        "web_results": state.web_results,
        "web_summary": state.web_summary,
        "web_follow_up_questions": state.web_follow_up_questions,
        "web_query": state.web_query,
        "web_provider": state.web_provider,
        "web_response_id": state.web_response_id,
        "web_documents": state.web_documents,
        "event_proposal": event_proposal,
        "activated_skills": state.activated_skills,
    }


def _extract_event_proposal(content: str) -> Optional[Dict[str, Any]]:
    if not content:
        return None

    def _parse_candidate(raw_text: str) -> Optional[Dict[str, Any]]:
        if not raw_text:
            return None
        cleaned = raw_text.strip()
        if cleaned.startswith("```") and cleaned.endswith("```"):
            cleaned = cleaned.strip("`").strip()
        try:
            parsed = json.loads(cleaned)
        except Exception:
            return None
        return _normalize_event_proposal(parsed)

    start = content.find(EVENT_PROPOSAL_START)
    end = content.find(EVENT_PROPOSAL_END)
    if start != -1 and end != -1 and end > start:
        body = content[start + len(EVENT_PROPOSAL_START) : end]
        candidate = _parse_candidate(body)
        if candidate:
            return candidate

    # Fallback: try to parse the first JSON object present
    first_brace = content.find("{")
    last_brace = content.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        fallback = _parse_candidate(content[first_brace : last_brace + 1])
        if fallback:
            return fallback
    return None


def _normalize_event_proposal(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None

    def _parse_dt(value: Any) -> Optional[datetime]:
        if isinstance(value, str) and value.strip():
            try:
                return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except Exception:
                return None
        if isinstance(value, datetime):
            return value
        return None

    start_dt = _parse_dt(raw.get("startDate") or raw.get("start_date"))
    if not start_dt:
        return None

    end_dt = _parse_dt(raw.get("endDate") or raw.get("end_date"))

    def _string_list(value: Any) -> List[str]:
        if not isinstance(value, (list, tuple)):
            return []
        cleaned: List[str] = []
        for item in value:
            if isinstance(item, (str, int, float)):
                text = str(item).strip()
                if text:
                    cleaned.append(text)
        return cleaned

    def _clamp_confidence(value: Any) -> Optional[float]:
        try:
            num = float(value)
        except (TypeError, ValueError):
            return None
        if num < 0:
            num = 0.0
        if num > 1:
            num = 1.0
        return num

    title = (raw.get("title") or "").strip() or None
    summary = (raw.get("summary") or "").strip() or None
    place = (raw.get("place") or "").strip() or None
    place_id = (raw.get("placeId") or raw.get("place_id") or "").strip() or None
    confidence = _clamp_confidence(raw.get("confidence"))
    missing = _string_list(raw.get("missing"))

    normalized = {
        "title": title,
        "start_date": start_dt.isoformat(),
        "end_date": end_dt.isoformat() if end_dt else None,
        "summary": summary,
        "people": _string_list(raw.get("people")),
        "tags": _string_list(raw.get("tags")),
        "types": _string_list(raw.get("types")),
        "place": place,
        "place_id": place_id,
        "confidence": confidence,
        "missing": missing,
        "raw": raw,
    }
    return normalized


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
