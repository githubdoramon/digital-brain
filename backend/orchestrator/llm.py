"""
LLM orchestration module.

This is the main entry point for LLM interactions. It coordinates:
- llm_tools: Tool definitions and handlers
- llm_prompts: Message building and context injection
- llm_agent: Agent loop logic and response finalization
"""

from __future__ import annotations

import json
import os
from time import perf_counter
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
import requests

import conversations
import skills

# Import from refactored modules
from llm_tools import TOOLS, AgentState, handle_tool_call
from llm_prompts import build_messages, get_time_context
from llm_agent import (
    MAX_ITERATIONS,
    MAX_CONTINUATION_RETRIES,
    looks_like_continuation,
    create_continuation_nudge,
    create_thinking_nudge,
    extract_event_proposal,
    finalize_bundle,
)

# Configuration
OLLAMA_HOST = os.getenv("OLLAMA_HOST")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))

# Validate required configuration
if not OLLAMA_HOST:
    raise RuntimeError("OLLAMA_HOST environment variable is required")
if not OLLAMA_CHAT_MODEL:
    raise RuntimeError("OLLAMA_CHAT_MODEL environment variable is required")
if not OLLAMA_EMBED_MODEL:
    raise RuntimeError("OLLAMA_EMBED_MODEL environment variable is required")


def _log_timing(label: str, start_time: float, **metadata: Any) -> None:
    """Log timing information for performance monitoring."""
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
        print(f"[timing] {label} {elapsed:.3f}s {meta_str}")
    else:
        print(f"[timing] {label} {elapsed:.3f}s")


# ---------------------------------------------------------------------------
# Ollama API calls
# ---------------------------------------------------------------------------

def _ollama_chat(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Make a synchronous chat request to Ollama."""
    payload = {
        "model": OLLAMA_CHAT_MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "stream": False,
    }
    resp = requests.post(
        f"{OLLAMA_HOST}/api/chat",
        json=payload,
        timeout=OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


async def _ollama_chat_stream(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
) -> AsyncGenerator[Dict[str, Any], None]:
    """Stream chat responses from Ollama."""
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


# ---------------------------------------------------------------------------
# Main agent functions
# ---------------------------------------------------------------------------

async def answer_question(
    question: str,
    search_limit: int = 3,
    user_id: str = "default_user",
    session_id: Optional[str] = None,
    user_email: Optional[str] = None,
    event_capture_enabled: bool = False,
) -> Dict[str, Any]:
    """
    Answer a question using the LLM with tool calling.

    This is the main non-streaming entry point.

    Args:
        question: The user's question
        search_limit: Maximum search results to return
        user_id: User identifier
        session_id: Conversation session ID
        user_email: User's email for context
        event_capture_enabled: Whether to extract event proposals

    Returns:
        Response bundle with answer and metadata
    """
    total_start = perf_counter()
    time_context = get_time_context()
    state = AgentState()

    # Load conversation history
    conversation_history: List[Dict[str, str]] = []
    if session_id and user_email:
        history_start = perf_counter()
        try:
            conversation_history = conversations.get_conversation_history(session_id, user_email)
            _log_timing("session.load_history", history_start, messages=len(conversation_history))
            print(f"[session] Retrieved {len(conversation_history)} messages from session {session_id}")
        except Exception as exc:
            _log_timing("session.load_history.error", history_start)
            print(f"[session] Failed to load history for session {session_id}: {exc}")

    # Build messages
    build_start = perf_counter()
    messages = build_messages(
        question,
        search_limit,
        conversation_history,
        user_email=user_id,
        current_time_context=time_context,
        event_capture_enabled=event_capture_enabled,
        state=state,
    )
    _log_timing("agent.build_messages", build_start, message_count=len(messages))

    # Agent loop
    iteration = 0
    thinking_retries = 0
    continuation_retries = 0

    while True:
        iteration += 1
        loop_start = perf_counter()
        print(f"[agent] iteration {iteration} start")

        # Call LLM
        chat_start = perf_counter()
        response = _ollama_chat(messages)
        _log_timing("ollama.chat", chat_start, iteration=iteration)

        message = response.get("message") or {}
        print(f"[agent] received message -> {json.dumps(message, ensure_ascii=False, default=str)[:500]}")

        if not message:
            raise RuntimeError(f"Unexpected Ollama response: {response}")

        # Handle tool calls
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            call_names = [call.get("function", {}).get("name") for call in tool_calls]
            print(f"[agent] tool calls requested: {call_names}")
            continuation_retries = 0  # Reset on successful tool call

            messages.append(message)
            for call in tool_calls:
                tool_result = handle_tool_call(call, state, question, search_limit)
                print(f"[agent] tool result: {json.dumps(tool_result, ensure_ascii=False, default=str)[:500]}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(tool_result, ensure_ascii=False, default=str),
                })

            _log_timing("agent.iteration", loop_start, iteration=iteration, event="tool_calls", tools=len(tool_calls))
            continue

        # No tool calls - check content
        content = (message.get("content") or "").strip()
        thinking = (message.get("thinking") or "").strip()

        # Handle empty content with thinking
        if not content:
            if thinking:
                thinking_retries += 1
                if thinking_retries > 3:
                    raise RuntimeError("Assistant returned internal reasoning multiple times without a final answer.")
                messages.append(create_thinking_nudge())
                continue
            raise RuntimeError(f"Unexpected Ollama response: {response}")

        thinking_retries = 0

        # Check max iterations
        if iteration >= MAX_ITERATIONS:
            print(f"[agent] Max iterations ({MAX_ITERATIONS}) reached, forcing final answer")
            # Fall through to finalize

        # Check for continuation intent
        elif looks_like_continuation(content) and continuation_retries < MAX_CONTINUATION_RETRIES:
            continuation_retries += 1
            print(f"[agent] Detected continuation intent, retry {continuation_retries}/{MAX_CONTINUATION_RETRIES}: {content[:100]}...")
            messages.append(message)
            messages.append(create_continuation_nudge())
            continue

        # Finalize response
        messages.append(message)
        event_proposal = extract_event_proposal(content) if event_capture_enabled else None

        # Persist conversation
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
                if (
                    persist_result.get("message_count_before", 0) == 0
                    and conversations.is_default_title(persist_result.get("previous_title"))
                ):
                    generated_title = _generate_thread_title(question)
                    if generated_title:
                        updated = conversations.update_thread_title(session_id, user_email, generated_title)
                        if updated:
                            new_thread_title = updated.get("title")
                            print(f"[session] Updated thread title: {new_thread_title!r}")
            except Exception as exc:
                print(f"[session] Failed to persist exchange: {exc}")

        # Build bundle
        bundle = finalize_bundle(
            question,
            content,
            state,
            search_limit,
            session_id,
            event_proposal=event_proposal,
        )
        if new_thread_title:
            bundle["thread_title"] = new_thread_title

        _log_timing("agent.iteration", loop_start, iteration=iteration, event="final", answer_chars=len(content))
        _log_timing("answer_question.total", total_start, iterations=iteration)

        print(f"[agent] final bundle -> {json.dumps(bundle, ensure_ascii=False, default=str)[:500]}")
        return bundle


async def answer_question_stream(
    question: str,
    search_limit: int = 3,
    user_id: str = "default_user",
    session_id: Optional[str] = None,
    user_email: Optional[str] = None,
    event_capture_enabled: bool = False,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Stream LLM responses with tool calling support.

    Yields events:
    - {"type": "token", "content": "..."} - Text chunks
    - {"type": "clear_content"} - Clear previous content (tool call starting)
    - {"type": "tool_call", "name": "...", "args": {...}} - Tool invocations
    - {"type": "tool_result", "name": "...", "result": {...}} - Tool outputs
    - {"type": "status", "message": "..."} - Status updates
    - {"type": "done", "bundle": {...}} - Final complete response

    Args:
        question: The user's question
        search_limit: Maximum search results
        user_id: User identifier
        session_id: Conversation session ID
        user_email: User's email for context
        event_capture_enabled: Whether to extract event proposals
    """
    total_start = perf_counter()
    time_context = get_time_context()
    state = AgentState()

    # Load conversation history
    conversation_history: List[Dict[str, str]] = []
    if session_id and user_email:
        try:
            conversation_history = conversations.get_conversation_history(session_id, user_email)
            print(f"[session] Retrieved {len(conversation_history)} messages")
        except Exception as exc:
            print(f"[session] Failed to load history: {exc}")

    # Build messages
    messages = build_messages(
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
    continuation_retries = 0

    while True:
        iteration += 1
        yield {"type": "status", "message": f"Thinking (iteration {iteration})..."}

        tool_calls = []
        current_content = ""
        streamed_any_content = False

        # Stream from Ollama
        async for chunk in _ollama_chat_stream(messages, TOOLS):
            message = chunk.get("message", {})

            # Stream content tokens
            delta = message.get("content", "")
            if delta:
                current_content += delta
                yield {"type": "token", "content": delta}
                streamed_any_content = True

            # Collect tool calls
            chunk_tool_calls = message.get("tool_calls")
            if chunk_tool_calls:
                tool_calls.extend(chunk_tool_calls)

            if chunk.get("done"):
                break

        # Handle tool calls
        if tool_calls:
            if streamed_any_content:
                yield {"type": "clear_content"}
            continuation_retries = 0

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

                # Handle skill scripts with streaming
                if func_name == "run_skill_script":
                    result = await _run_skill_script_streaming(call, state)
                    # Yield any status messages from the script
                    for status_msg in result.pop("_status_messages", []):
                        yield {"type": "status", "message": status_msg}
                else:
                    result = handle_tool_call(call, state, question, search_limit)

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

        # Check max iterations
        if iteration >= MAX_ITERATIONS:
            print(f"[agent] Max iterations ({MAX_ITERATIONS}) reached")

        # Check for continuation intent
        elif looks_like_continuation(current_content) and continuation_retries < MAX_CONTINUATION_RETRIES:
            continuation_retries += 1
            print(f"[agent] Detected continuation intent, retry {continuation_retries}/{MAX_CONTINUATION_RETRIES}")

            if streamed_any_content:
                yield {"type": "clear_content"}

            messages.append({"role": "assistant", "content": current_content})
            messages.append(create_continuation_nudge())
            continue

        # Final answer
        accumulated_content = current_content
        break

    # Finalize
    event_proposal = extract_event_proposal(accumulated_content) if event_capture_enabled else None

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
            if (
                persist_result.get("message_count_before", 0) == 0
                and conversations.is_default_title(persist_result.get("previous_title"))
            ):
                generated_title = _generate_thread_title(question)
                if generated_title:
                    updated = conversations.update_thread_title(session_id, user_email, generated_title)
                    if updated:
                        new_thread_title = updated.get("title")
        except Exception as exc:
            print(f"[session] Failed to persist exchange: {exc}")

    # Build final bundle
    bundle = finalize_bundle(
        question,
        accumulated_content,
        state,
        search_limit,
        session_id,
        event_proposal=event_proposal,
    )
    if new_thread_title:
        bundle["thread_title"] = new_thread_title

    _log_timing("answer_question_stream.total", total_start, iterations=iteration)

    yield {"type": "done", "bundle": bundle}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

async def _run_skill_script_streaming(
    tool_call: Dict[str, Any],
    state: AgentState,
) -> Dict[str, Any]:
    """
    Run a skill script with streaming status support.

    Returns the result dict with an additional _status_messages key
    containing any status messages emitted during execution.
    """
    function = tool_call.get("function") or {}
    raw_args = function.get("arguments") or "{}"

    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except json.JSONDecodeError:
        return {"error": f"Invalid arguments: {raw_args}"}

    skill_name = args.get("skill_name")
    script_name = args.get("script_name")
    script_args = args.get("args") or {}

    if not skill_name or not script_name:
        return {"error": "skill_name and script_name are required"}

    # Verify skill is active
    active_skill_names = [s.get("name") for s in state.activated_skills]
    if skill_name not in active_skill_names:
        return {"error": f"Skill '{skill_name}' is not active"}

    registry = skills.get_registry()
    skill = registry.get_skill(skill_name)
    if not skill:
        return {"error": f"Skill '{skill_name}' not found"}

    print(f"[agent] running skill script: {skill_name}/{script_name}")
    step_start = perf_counter()

    runner = skills.get_runner_for_skill(skill.path)
    status_messages = []

    async def collect_status(line: str) -> None:
        if line.startswith("STATUS: "):
            status_messages.append(line[8:])

    result = await runner.run_script_streaming(
        script_name,
        script_args,
        on_output_async=collect_status,
    )

    _log_timing("tool.run_skill_script", step_start, skill=skill_name, script=script_name)

    result["_status_messages"] = status_messages
    return result


def _generate_thread_title(question: str) -> Optional[str]:
    """Generate a thread title from the first question."""
    prompt = (
        "Generate a very short title (3-6 words) for a conversation that starts with this question. "
        "Return ONLY the title, no quotes or explanation.\n\n"
        f"Question: {question}"
    )

    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model": OLLAMA_CHAT_MODEL,
                "prompt": prompt,
                "stream": False,
            },
            timeout=30,
        )
        response.raise_for_status()
        title = response.json().get("response", "").strip()

        # Clean up the title
        title = title.strip('"\'')
        if len(title) > 100:
            title = title[:97] + "..."

        return title if title else None
    except Exception as exc:
        print(f"[agent] Failed to generate thread title: {exc}")
        return None
