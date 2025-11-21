from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict, List, Set, Optional

import requests
from mem0 import AsyncMemory

import conversations
import retrieval
import sql_tools
import web_tools

OLLAMA_HOST = os.getenv("OLLAMA_HOST")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))  # Keep default for timeout
QDRANT_HOST = os.getenv("QDRANT_HOST")
QDRANT_PORT = int(os.getenv("QDRANT_PORT")) if os.getenv("QDRANT_PORT") else None

# Validate required configuration
if not OLLAMA_HOST:
    raise RuntimeError("OLLAMA_HOST environment variable is required")
if not OLLAMA_CHAT_MODEL:
    raise RuntimeError("OLLAMA_CHAT_MODEL environment variable is required")
if not OLLAMA_EMBED_MODEL:
    raise RuntimeError("OLLAMA_EMBED_MODEL environment variable is required")
if not QDRANT_HOST:
    raise RuntimeError("QDRANT_HOST environment variable is required")
if not QDRANT_PORT:
    raise RuntimeError("QDRANT_PORT environment variable is required")

print(f"Using Ollama chat model: {OLLAMA_CHAT_MODEL}")
print(f"Using Ollama embed model: {OLLAMA_EMBED_MODEL}")
print(f"Using Qdrant at {QDRANT_HOST}:{QDRANT_PORT}")


def _shorten(text: Any, limit: int = 120) -> str:
    if not isinstance(text, str):
        return str(text)
    single_line = text.replace("\n", " ").strip()
    if len(single_line) <= limit:
        return single_line
    return single_line[: limit - 3] + "..."


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


# Mem0 configuration for conversation memory with persistent Qdrant storage
MEM0_CONFIG = {
    "llm": {
        "provider": "ollama",
        "config": {
            "model": OLLAMA_CHAT_MODEL,
            "ollama_base_url": OLLAMA_HOST,
        }
    },
    "embedder": {
        "provider": "ollama",
        "config": {
            "model": OLLAMA_EMBED_MODEL,
            "ollama_base_url": OLLAMA_HOST,
            "embedding_dims": 768,  # nomic-embed-text outputs 768-dimensional vectors
        }
    },
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "host": QDRANT_HOST,
            "port": QDRANT_PORT,
            "collection_name": "digital_brain_interaction_memories",
            "embedding_model_dims": 768,  # nomic-embed-text outputs 768-dimensional vectors
        }
    },
    "version": "v1.1"
}

# Initialize Mem0 async memory instance
_memory_instance: Optional[AsyncMemory] = None

async def get_memory() -> Optional[AsyncMemory]:
    """Get or create the Mem0 async memory instance."""
    global _memory_instance
    if _memory_instance is None:
        try:
            _memory_instance = await AsyncMemory.from_config(MEM0_CONFIG)
            print("[mem0] AsyncMemory instance initialized successfully")
        except Exception as e:
            print(f"[mem0] Warning: Failed to initialize Mem0: {e}")
            print("[mem0] Continuing without memory layer")
    return _memory_instance

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
) -> Dict[str, Any]:
    total_start = perf_counter()
    question_preview = _shorten(question)
    current_utc = datetime.now(timezone.utc)
    local_now = current_utc.astimezone()
    time_context = (
        "Current time context available to you:\n"
        f"- UTC now: {current_utc.isoformat()}\n"
        f"- Local system time: {local_now.isoformat()}"
    )
    print(
        f"[agent] answer_question start user_id={user_id} session_id={session_id} "
        f"search_limit={search_limit} question={question_preview!r} utc={current_utc.isoformat()}"
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

    # Retrieve relevant long-term memories from Mem0 (await this)
    memories_used: List[str] = []
    memory_start = perf_counter()
    memory = await get_memory()
    _log_timing(
        "mem0.get_memory",
        memory_start,
        session_id=session_id,
        available=bool(memory),
    )
    if memory:
        search_start: Optional[float] = None
        try:
            print(f"[mem0] Retrieving long-term memories for user_id={user_id}")
            search_start = perf_counter()
            response = await memory.search(question, user_id=user_id, limit=5)

            # Mem0 returns {"results": [{"id": ..., "memory": "...", ...}, ...]}
            if isinstance(response, dict) and "results" in response:
                results = response["results"]
                if isinstance(results, list):
                    for result in results:
                        if isinstance(result, dict) and "memory" in result:
                            memories_used.append(result["memory"])

                    if memories_used:
                        print(f"[mem0] Found {len(memories_used)} relevant long-term memories")
                    else:
                        print("[mem0] No long-term memories found for this query")
                else:
                    print(f"[mem0] Unexpected results format: {type(results)}")
            else:
                print(f"[mem0] Unexpected response format: {type(response)}")
            _log_timing(
                "mem0.search",
                search_start,
                results=len(memories_used),
            )
        except Exception as e:
            if search_start is not None:
                _log_timing("mem0.search.error", search_start)
            print(f"[mem0] Error retrieving memories: {e}")
            import traceback
            traceback.print_exc()

    build_start = perf_counter()
    messages: List[Dict[str, Any]] = _build_messages(
        question,
        search_limit,
        memories_used,
        conversation_history,
        user_email=user_id,
        current_time_context=time_context,
    )
    _log_timing("agent.build_messages", build_start, message_count=len(messages))

    iteration = 0
    thinking_retries = 0

    while True:
        iteration += 1
        loop_start = perf_counter()
        print(f"[agent] iteration {iteration} start")
        print("[agent] sending messages ->", json.dumps(messages, ensure_ascii=False, indent=2))
        chat_start = perf_counter()
        response = _ollama_chat(messages)
        _log_timing("ollama.chat", chat_start, iteration=iteration)
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
                thinking_preview = _shorten(thinking)
                print(
                    f"[agent] received thinking without content (retry {thinking_retries}) -> {thinking_preview!r}"
                )
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

        # Update session store with this Q&A pair
        new_thread_title: Optional[str] = None
        if session_id and user_email:
            try:
                persist_result = conversations.record_exchange(
                    thread_id=session_id,
                    user_email=user_email,
                    user_message=question,
                    assistant_message=content,
                    user_metadata={},
                    assistant_metadata={"memories_used": memories_used} if memories_used else {},
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
        bundle = _finalize_bundle(question, content, state, search_limit, session_id, memories_used)
        if new_thread_title:
            bundle["thread_title"] = new_thread_title

        # Store the last Q&A pair in mem0 for long-term memory extraction (fire and forget)
        # Let mem0 decide what's worth remembering
        if memory:
            # Fire and forget - don't await, let it run in background
            asyncio.create_task(_store_memory_async(memory, question, content, user_id, session_id))

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

        print("[agent] final bundle ->", json.dumps(bundle, ensure_ascii=False, indent=2))
        _log_timing(
            "answer_question.total",
            total_start,
            iterations=iteration,
            session_id=session_id,
            user_id=user_id,
        )
        return bundle


async def _store_memory_async(
    memory: AsyncMemory,
    question: str,
    content: str,
    user_id: str,
    session_id: Optional[str]
) -> None:
    """Store memory in background without blocking the response."""
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        
        # Pass just the last Q&A pair to mem0, let it extract insights
        last_exchange = [
            {"role": "user", "content": question},
            {"role": "assistant", "content": content}
        ]
        
        store_start = perf_counter()
        result = await memory.add(
            last_exchange,
            user_id=user_id,
            metadata={"session_id": session_id, "timestamp": timestamp},
        )
        _log_timing("mem0.store", store_start, session_id=session_id, user_id=user_id)
        print(f"[mem0] Stored Q&A pair for long-term memory extraction, result: {result}")
    except Exception as e:
        print(f"[mem0] Error storing in long-term memory: {e}")
        import traceback
        traceback.print_exc()


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
    memories_used: List[str] = None,
    conversation_history: List[Dict[str, str]] = None,
    user_email: Optional[str] = None,
    current_time_context: Optional[str] = None,
) -> List[Dict[str, str]]:
    system_prompt = (
        "You are a personal memory assistant. Your goal is to make the user feel like they are talking to a real person, not a robot. "
        "Use the available tools to gather accurate context before answering. "
        "Do not fabricate events; if no relevant memories exist, say so. "
        "You are talking to a human, therefore avoid replying with ids or other technical details, and prefer using names, titles for things you find in databases. "
        f"When searching, prefer returning at most {search_limit} highly relevant results, unless the user is requesting full data."
    )
    protocol_prompt = "Tool protocol: when a question references stored events, memories, contacts, places, timelines, or relationships, " \
        "first refresh the schema with describe_schema, then plan any execute_sql queries needed to retrieve facts. " \
        "Be aware the database is personal to the user themselves, so everything present there has a relation to the person asking the question, so you don't need to overthing to find ids only related to the logged user." \
        "Cross-check every table or column in a planned SQL statement against the schema snapshot; never invent new tables. Make sure you query things in a case insensitive way. " \
        "Use resolve_query when entity or time extraction helps craft structured constraints. " \
        "For contacts, make sure you search using different strategies when it comes to names, first names, last names, full names, partial names, nicknames, aliases, and so on. " \
        "For relationship closeness questions, use the `contact_relationships` table to understand interpersonal links. " \
        "For events, meetings, moments, use the `events` table to retrieve the event details. " \
        "Tasks or to dos are on the 'todos' table, and might have relations to events, contacts or places." \
        "Do not stop after describing a plan—actually call the necessary tools, inspect their outputs, and base your final answer on that evidence. " \
        "If a SQL attempt fails validation, revise and retry until you either succeed or can explain why the data cannot be retrieved. " \
        "Only respond after you have gathered sufficient evidence from the tools. " \
        "If you are not 100% sure, still respond to the original question, but state your uncertainty instead of declining outright."

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]

    schema_hint = _load_schema_hint()
    if schema_hint:
        messages.append({"role": "system", "content": schema_hint})

    # Self context based on authenticated email
    if user_email:
        self_context = _self_context_from_email(user_email)
        if self_context:
            messages.append({"role": "system", "content": self_context})

    # Add long-term memories from previous interactions if available
    if memories_used:
        memory_context = "Relevant long-term knowledge about the user:\n" + "\n".join(f"- {mem}" for mem in memories_used)
        messages.append({"role": "system", "content": memory_context})
        print(f"[mem0] Added {len(memories_used)} long-term memories to context")

    messages.append({"role": "system", "content": protocol_prompt})
    
    if current_time_context:
        messages.append({"role": "system", "content": current_time_context})
    
    # Add conversation history (short-term session memory)
    if conversation_history:
        for msg in conversation_history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        print(f"[session] Added {len(conversation_history)} messages from session history")
    
    messages.append({"role": "user", "content": question.strip()})
    return messages


def _self_context_from_email(email: str) -> Optional[str]:
    contact = retrieval.get_contact_by_email(email)
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
        limit = max(1, min(limit, search_limit))
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
        events = retrieval.get_events([str(i) for i in ids])
        _log_timing("tool.get_events", step_start, count=len(events))
        state.update_detailed_events(events)
        return {"events": events}

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
        unknown_tables = _find_unknown_tables(query)
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
                    "available_tables": sorted(_get_schema_snapshot().keys()),
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

    raise RuntimeError(f"Unsupported tool requested: {name}")


def _load_schema_hint() -> str:
    global _SCHEMA_HINT_CACHE
    if _SCHEMA_HINT_CACHE is not None:
        return _SCHEMA_HINT_CACHE

    snapshot = _get_schema_snapshot()
    if not snapshot:
        _SCHEMA_HINT_CACHE = ""
        return ""

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


def _finalize_bundle(
    question: str,
    answer: str,
    state: AgentState,
    search_limit: int,
    session_id: Optional[str],
    memories_used: List[str] = None,
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
        ids = [row.get("id") for row in state.search_results if row.get("id")]
        if ids:
            events_start = perf_counter()
            events = retrieval.get_events(ids)
            _log_timing("pipeline.get_events", events_start, count=len(events))
            state.update_detailed_events(events)

    return {
        "question": question,
        "answer": answer,
        "resolution": state.resolution or {},
        "search_results": state.search_results,
        "detailed_events": state.detailed_events,
        "session_id": session_id,
        "thread_id": session_id,
        "memories_used": memories_used or [],
        "web_results": state.web_results,
        "web_summary": state.web_summary,
        "web_follow_up_questions": state.web_follow_up_questions,
        "web_query": state.web_query,
        "web_provider": state.web_provider,
        "web_response_id": state.web_response_id,
        "web_documents": state.web_documents,
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
