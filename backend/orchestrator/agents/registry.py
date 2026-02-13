"""Conversational profile registry and intent-to-profile dispatch."""

from __future__ import annotations

from functools import lru_cache

from agent.agent_interfaces import ConversationalAgentInterface
from agent.router import IntentClassification
from agents.main.agent import build_main_conversational_agent
from agents.memory_expert.agent import build_memory_expert_conversational_agent


@lru_cache(maxsize=8)
def _build_profile_interfaces(
    *,
    max_steps: int,
    max_tool_calls: int,
    timeout_seconds: int,
) -> dict[str, ConversationalAgentInterface]:
    main = build_main_conversational_agent(
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        timeout_seconds=timeout_seconds,
    )
    memory_expert = build_memory_expert_conversational_agent(
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        timeout_seconds=timeout_seconds,
    )
    return {
        "main": main,
        "memory_expert": memory_expert,
    }


def build_conversational_profile_registry(
    *,
    max_steps: int,
    max_tool_calls: int,
    timeout_seconds: int,
) -> dict[str, ConversationalAgentInterface]:
    """Build or reuse profile interfaces for current runtime config."""
    return _build_profile_interfaces(
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        timeout_seconds=timeout_seconds,
    )


def choose_profile_interface(
    classification: IntentClassification | None,
    profiles: dict[str, ConversationalAgentInterface],
) -> ConversationalAgentInterface:
    """Resolve conversational profile interface from routed intent."""
    fallback_interface = next(
        (profile for profile in profiles.values() if profile.is_fallback),
        None,
    )
    if fallback_interface is None:
        raise RuntimeError("Conversational profile registry is missing a fallback profile")

    if classification is None:
        return fallback_interface

    for profile_interface in profiles.values():
        if profile_interface.is_fallback:
            continue
        if profile_interface.supports_intent(classification.intent):
            return profile_interface
    return fallback_interface
