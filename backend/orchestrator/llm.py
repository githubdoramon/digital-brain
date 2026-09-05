"""
LLM orchestration module.

Main entry point for LLM interactions using the bounded agent controller.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import conversations
from llm_config import get_fast_model, get_smart_model
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

LLM_BASE_URL = os.getenv("LLM_BASE_URL")

if not LLM_BASE_URL:
    raise RuntimeError("LLM_BASE_URL environment variable is required")
get_fast_model()
get_smart_model()

logger.info("[llm] Bounded agent architecture ENABLED")


_main_controller = None


def _extract_recent_resolved_place(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    raw_place = metadata.get("resolved_place")
    if not isinstance(raw_place, dict):
        return None
    place_id = str(raw_place.get("place_id") or "").strip()
    if not place_id:
        return None
    return {
        "place_id": place_id,
        "place_name": str(raw_place.get("place_name") or "").strip() or None,
        "address": str(raw_place.get("address") or "").strip() or None,
        "city": str(raw_place.get("city") or "").strip() or None,
        "country": str(raw_place.get("country") or "").strip() or None,
        "role_hint": str(raw_place.get("role_hint") or "").strip() or None,
        "source": str(raw_place.get("source") or "").strip() or None,
    }


def _merge_client_context_with_recent_place(
    *,
    client_context: dict[str, Any] | None,
    latest_assistant_metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    recent_place = _extract_recent_resolved_place(latest_assistant_metadata)
    if not recent_place:
        return client_context

    merged: dict[str, Any] = {}
    if isinstance(client_context, dict):
        merged.update(client_context)
    merged["recent_resolved_place"] = recent_place
    return merged


def _extract_resolved_place_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    resolution = result.get("resolution")
    if not isinstance(resolution, dict):
        return None
    raw_place = resolution.get("resolved_place")
    if not isinstance(raw_place, dict):
        return None
    place_id = str(raw_place.get("place_id") or "").strip()
    if not place_id:
        return None
    return {
        "place_id": place_id,
        "place_name": str(raw_place.get("place_name") or "").strip() or None,
        "address": str(raw_place.get("address") or "").strip() or None,
        "city": str(raw_place.get("city") or "").strip() or None,
        "country": str(raw_place.get("country") or "").strip() or None,
        "role_hint": str(raw_place.get("role_hint") or "").strip() or None,
        "source": str(raw_place.get("source") or "").strip() or None,
    }


def _get_main_controller():
    """Create/reuse the conversational controller instance."""
    global _main_controller
    if _main_controller is None:
        from agent.controller import AgentController
        from agent.limits import AgentConfig
        from agents.main.agent import build_main_conversational_agent

        config = AgentConfig.from_env()
        timeout_seconds = int(os.getenv("LLM_TIMEOUT", "120"))
        main_agent = build_main_conversational_agent(
            max_steps=config.max_steps,
            max_tool_calls=config.max_tool_calls,
            timeout_seconds=timeout_seconds,
        )
        _main_controller = AgentController(
            config=config,
            conversational_agent=main_agent,
        )
    return _main_controller


# ---------------------------------------------------------------------------
# Main agent functions
# ---------------------------------------------------------------------------


async def answer_question(
    question: str,
    search_limit: int = 30,
    user_id: str = "default_user",
    session_id: str | None = None,
    user_email: str | None = None,
    client_context: dict[str, Any] | None = None,
    ui_submission: dict[str, Any] | None = None,
    response_modality: str | None = None,
    on_exchange_persisted: Any | None = None,
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
        client_context: Context from client (timezone/locale/location)
        ui_submission: Optional structured UI action or fallback text from client

    Returns:
        Response bundle with answer and metadata
    """
    # Load conversation history
    conversation_history: list[dict[str, str]] = []
    latest_assistant_metadata: dict[str, Any] | None = None
    if session_id and user_email:
        try:
            conversation_history = conversations.get_conversation_history(session_id, user_email)
            latest_assistant_metadata = conversations.get_latest_assistant_metadata(
                session_id,
                user_email,
            )
        except Exception as exc:
            logger.warning("[session] Failed to load history: %s", exc, exc_info=exc)

    effective_client_context = _merge_client_context_with_recent_place(
        client_context=client_context,
        latest_assistant_metadata=latest_assistant_metadata,
    )

    controller = _get_main_controller()
    result = await controller.run(
        question=question,
        user_id=user_id,
        session_id=session_id,
        user_email=user_email,
        conversation_history=conversation_history,
        search_limit=search_limit,
        client_context=effective_client_context,
        ui_submission=ui_submission,
        response_modality=response_modality,
    )

    from voice_response import ResponseModality, normalize_modality

    if normalize_modality(response_modality) is ResponseModality.VOICE and result.get("answer"):
        from voice_response import prepare_voice_answer

        result["answer"] = await prepare_voice_answer(result["answer"])

    # Persist conversation (bounded agent doesn't do this internally)
    if session_id and user_email and result.get("answer"):
        try:
            assistant_metadata = {}
            if result.get("ui_directives"):
                assistant_metadata["ui_directives"] = result["ui_directives"]
            linked_items = result.get("linked_items")
            if isinstance(linked_items, list) and linked_items:
                assistant_metadata["linked_items"] = linked_items
            generated_files = result.get("generated_files")
            if isinstance(generated_files, list) and generated_files:
                assistant_metadata["generated_files"] = generated_files
            resolved_place = _extract_resolved_place_from_result(result)
            if resolved_place:
                assistant_metadata["resolved_place"] = resolved_place

            persist_result = conversations.record_exchange(
                thread_id=session_id,
                user_email=user_email,
                user_message=question,
                assistant_message=result["answer"],
                user_metadata={},
                assistant_metadata=assistant_metadata,
            )

            # Generate title for new threads
            if persist_result.get(
                "message_count_before", 0
            ) == 0 and conversations.is_default_title(persist_result.get("previous_title")):
                generated_title = _generate_thread_title(question)
                if generated_title:
                    updated = conversations.update_thread_title(
                        session_id, user_email, generated_title
                    )
                    if updated:
                        result["thread_title"] = updated.get("title")

            # Trigger background fact extraction
            if on_exchange_persisted:
                on_exchange_persisted(
                    user_email=user_email,
                    user_message=question,
                    assistant_message=result["answer"],
                    thread_id=session_id,
                )
        except Exception as exc:
            logger.warning("[session] Failed to persist exchange: %s", exc, exc_info=exc)

    return result


async def answer_question_stream(
    question: str,
    search_limit: int = 30,
    user_id: str = "default_user",
    session_id: str | None = None,
    user_email: str | None = None,
    client_context: dict[str, Any] | None = None,
    ui_submission: dict[str, Any] | None = None,
    response_modality: str | None = None,
    on_exchange_persisted: Any | None = None,
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
        client_context: Context from client (timezone/locale/location)
        ui_submission: Optional structured UI action or fallback text from client
    """
    # Load conversation history
    conversation_history: list[dict[str, str]] = []
    latest_assistant_metadata: dict[str, Any] | None = None
    if session_id and user_email:
        try:
            conversation_history = conversations.get_conversation_history(session_id, user_email)
            latest_assistant_metadata = conversations.get_latest_assistant_metadata(
                session_id,
                user_email,
            )
        except Exception as exc:
            logger.warning("[session] Failed to load history: %s", exc, exc_info=exc)

    effective_client_context = _merge_client_context_with_recent_place(
        client_context=client_context,
        latest_assistant_metadata=latest_assistant_metadata,
    )

    controller = _get_main_controller()
    final_bundle = None

    async for event in controller.run_stream(
        question=question,
        user_id=user_id,
        session_id=session_id,
        user_email=user_email,
        conversation_history=conversation_history,
        search_limit=search_limit,
        client_context=effective_client_context,
        ui_submission=ui_submission,
        response_modality=response_modality,
    ):
        if event.get("type") == "done":
            final_bundle = event.get("bundle", {})
            from voice_response import ResponseModality, normalize_modality

            if (
                normalize_modality(response_modality) is ResponseModality.VOICE
                and final_bundle.get("answer")
            ):
                from voice_response import prepare_voice_answer

                final_bundle["answer"] = await prepare_voice_answer(final_bundle["answer"])
                event["bundle"] = final_bundle

        yield event

    # Persist conversation after streaming completes
    if final_bundle and session_id and user_email and final_bundle.get("answer"):
        try:
            assistant_metadata = {}
            if final_bundle.get("ui_directives"):
                assistant_metadata["ui_directives"] = final_bundle["ui_directives"]
            linked_items = final_bundle.get("linked_items")
            if isinstance(linked_items, list) and linked_items:
                assistant_metadata["linked_items"] = linked_items
            generated_files = final_bundle.get("generated_files")
            if isinstance(generated_files, list) and generated_files:
                assistant_metadata["generated_files"] = generated_files
            resolved_place = _extract_resolved_place_from_result(final_bundle)
            if resolved_place:
                assistant_metadata["resolved_place"] = resolved_place

            persist_result = conversations.record_exchange(
                thread_id=session_id,
                user_email=user_email,
                user_message=question,
                assistant_message=final_bundle["answer"],
                user_metadata={},
                assistant_metadata=assistant_metadata,
            )

            # Generate title for new threads
            if persist_result.get(
                "message_count_before", 0
            ) == 0 and conversations.is_default_title(persist_result.get("previous_title")):
                generated_title = _generate_thread_title(question)
                if generated_title:
                    updated = conversations.update_thread_title(
                        session_id, user_email, generated_title
                    )
                    if updated:
                        # Yield title update event
                        yield {
                            "type": "title_update",
                            "title": updated.get("title"),
                        }

            # Trigger background fact extraction
            if on_exchange_persisted:
                on_exchange_persisted(
                    user_email=user_email,
                    user_message=question,
                    assistant_message=final_bundle["answer"],
                    thread_id=session_id,
                )
        except Exception as exc:
            logger.warning("[session] Failed to persist exchange: %s", exc, exc_info=exc)

    return


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _generate_thread_title(question: str) -> str | None:
    """Generate a thread title from the first question."""
    from llm_helpers import LLMUnavailableError, call_llm

    prompt = (
        "Generate a very short title (3-6 words) for a conversation that starts with this question. "
        "Return ONLY the title, no quotes or explanation.\n\n"
        f"Question: {question}"
    )

    try:
        title = call_llm(prompt, timeout=30).strip().strip("\"'")

        if len(title) > 100:
            title = title[:97] + "..."

        return title if title else None
    except Exception as exc:
        if isinstance(exc, LLMUnavailableError):
            logger.warning("[agent] LLM unavailable while generating thread title; leaving default title")
            return None
        logger.warning("[agent] Failed to generate thread title: %s", exc, exc_info=exc)
        return None
