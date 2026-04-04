"""Memory expert prompt/message construction helpers."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any

from agent.guardrails import build_contact_scope_context
from observability.logger import get_runtime_logger
from prompts.context import (
    get_location_context,
    get_self_context,
    get_tag_context,
    get_time_context,
    get_user_facts_context,
)
from prompts.state_injection import build_state_message

from .prompts import get_memory_expert_protocol_prompt, get_memory_expert_system_prompt

logger = get_runtime_logger(__name__)

if TYPE_CHECKING:
    from agent.state import AgentState


@lru_cache(maxsize=16)
def _cached_system_prompt(search_limit: int) -> str:
    return get_memory_expert_system_prompt(search_limit)


@lru_cache(maxsize=1)
def _cached_protocol_prompt() -> str:
    return get_memory_expert_protocol_prompt()


@lru_cache(maxsize=1)
def _cached_tag_context() -> str:
    return get_tag_context() or ""


def inject_memory_expert_skills(
    *,
    messages: list[dict[str, Any]],
    question: str,
    conversation_history: list[dict[str, str]] | None,
    state: AgentState,
) -> None:
    """Inject only matching skills to keep memory expert context compact."""
    try:
        import skills

        registry = skills.get_registry()
        matching_skills = registry.find_matching_skills(
            query=question,
            conversation_history=conversation_history,
        )
        for match in matching_skills:
            skill_prompt = (
                f"ACTIVE SKILL [{match.skill.name}] (confidence: {match.confidence:.2f}):\n"
                f"{match.skill.instructions}"
            )
            messages.append({"role": "system", "content": skill_prompt})
            state.activated_skills.append(
                {
                    "name": match.skill.name,
                    "confidence": match.confidence,
                }
            )
    except Exception as exc:
        logger.exception("[memory_expert.message_builder] Skills injection error: %s", exc)


def build_memory_expert_messages(
    *,
    question: str,
    state: AgentState,
    conversation_history: list[dict[str, str]] | None,
    user_email: str | None,
    search_limit: int,
    client_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build the message list for the memory expert conversational agent."""
    messages: list[dict[str, Any]] = []

    messages.append({"role": "system", "content": _cached_system_prompt(search_limit)})
    tags_context = _cached_tag_context()
    if tags_context:
        messages.append({"role": "system", "content": tags_context})
    messages.append({"role": "system", "content": _cached_protocol_prompt()})

    if user_email:
        self_context = get_self_context(user_email)
        if self_context:
            messages.append({"role": "system", "content": self_context})

        user_facts_ctx = get_user_facts_context(user_email, question)
        if user_facts_ctx:
            messages.append({"role": "system", "content": user_facts_ctx})

    messages.append({"role": "system", "content": get_time_context()})

    location_context = get_location_context(client_context)
    if location_context:
        messages.append({"role": "system", "content": location_context})

    inject_memory_expert_skills(
        messages=messages,
        question=question,
        conversation_history=conversation_history,
        state=state,
    )

    messages.append(build_state_message(state))

    contact_scope_context = build_contact_scope_context(
        state.resolution.get("active_contact_scope") or [],
        resolution_state=state.resolution,
    )
    if contact_scope_context:
        messages.append({"role": "system", "content": contact_scope_context})

    if conversation_history:
        for msg in conversation_history:
            messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": question.strip()})
    return messages
