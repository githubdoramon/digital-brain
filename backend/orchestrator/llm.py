"""
LLM orchestration module.

Main entry point for LLM interactions using the bounded agent controller.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import conversations

LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_CHAT_MODEL = os.getenv("LLM_CHAT_MODEL")

if not LLM_BASE_URL:
    raise RuntimeError("LLM_BASE_URL environment variable is required")
if not LLM_CHAT_MODEL:
    raise RuntimeError("LLM_CHAT_MODEL environment variable is required")

print("[llm] Bounded agent architecture ENABLED")


# ---------------------------------------------------------------------------
# Main agent functions
# ---------------------------------------------------------------------------

async def answer_question(
    question: str,
    search_limit: int = 30,
    user_id: str = "default_user",
    session_id: str | None = None,
    user_email: str | None = None,
) -> dict[str, Any]:
    """
    Answer a question using the LLM with tool calling.

    This is the main non-streaming entry point.

    Args:
        question: The user's question
        search_limit: Maximum search results to return
        user_id: User identifier
        session_id: Conversation session ID
        user_email: User's email for context

    Returns:
        Response bundle with answer and metadata
    """
    from agent.controller import get_controller

    # Load conversation history
    conversation_history: list[dict[str, str]] = []
    if session_id and user_email:
        try:
            conversation_history = conversations.get_conversation_history(session_id, user_email)
        except Exception as exc:
            print(f"[session] Failed to load history: {exc}")

    controller = get_controller()
    result = await controller.run(
        question=question,
        user_id=user_id,
        session_id=session_id,
        user_email=user_email,
        conversation_history=conversation_history,
        search_limit=search_limit,
    )

    # Persist conversation (bounded agent doesn't do this internally)
    if session_id and user_email and result.get("answer"):
        try:
            assistant_metadata = {}
            # Removed: event_proposal - use /event command instead

            persist_result = conversations.record_exchange(
                thread_id=session_id,
                user_email=user_email,
                user_message=question,
                assistant_message=result["answer"],
                user_metadata={},
                assistant_metadata=assistant_metadata,
            )

            # Generate title for new threads
            if (
                persist_result.get("message_count_before", 0) == 0
                and conversations.is_default_title(persist_result.get("previous_title"))
            ):
                generated_title = _generate_thread_title(question)
                if generated_title:
                    updated = conversations.update_thread_title(session_id, user_email, generated_title)
                    if updated:
                        result["thread_title"] = updated.get("title")
        except Exception as exc:
            print(f"[session] Failed to persist exchange: {exc}")

    return result


async def answer_question_stream(
    question: str,
    search_limit: int = 30,
    user_id: str = "default_user",
    session_id: str | None = None,
    user_email: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
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
    """
    from agent.controller import get_controller

    # Load conversation history
    conversation_history: list[dict[str, str]] = []
    if session_id and user_email:
        try:
            conversation_history = conversations.get_conversation_history(session_id, user_email)
        except Exception as exc:
            print(f"[session] Failed to load history: {exc}")

    controller = get_controller()
    final_bundle = None

    async for event in controller.run_stream(
        question=question,
        user_id=user_id,
        session_id=session_id,
        user_email=user_email,
        conversation_history=conversation_history,
        search_limit=search_limit,
    ):
        if event.get("type") == "done":
            final_bundle = event.get("bundle", {})

        yield event

    # Persist conversation after streaming completes
    if final_bundle and session_id and user_email and final_bundle.get("answer"):
        try:
            assistant_metadata = {}

            persist_result = conversations.record_exchange(
                thread_id=session_id,
                user_email=user_email,
                user_message=question,
                assistant_message=final_bundle["answer"],
                user_metadata={},
                assistant_metadata=assistant_metadata,
            )

            # Generate title for new threads
            if (
                persist_result.get("message_count_before", 0) == 0
                and conversations.is_default_title(persist_result.get("previous_title"))
            ):
                generated_title = _generate_thread_title(question)
                if generated_title:
                    updated = conversations.update_thread_title(session_id, user_email, generated_title)
                    if updated:
                        # Yield title update event
                        yield {
                            "type": "title_update",
                            "title": updated.get("title"),
                        }
        except Exception as exc:
            print(f"[session] Failed to persist exchange: {exc}")

    return


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _generate_thread_title(question: str) -> str | None:
    """Generate a thread title from the first question."""
    from llm_helpers import call_llm

    prompt = (
        "Generate a very short title (3-6 words) for a conversation that starts with this question. "
        "Return ONLY the title, no quotes or explanation.\n\n"
        f"Question: {question}"
    )

    try:
        title = call_llm(prompt, timeout=30).strip().strip('"\'')

        if len(title) > 100:
            title = title[:97] + "..."

        return title if title else None
    except Exception as exc:
        print(f"[agent] Failed to generate thread title: {exc}")
        return None
