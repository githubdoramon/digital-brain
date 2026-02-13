"""Tests for conversational profile dispatch."""

from agent.router import IntentClassification, IntentType
from agents.registry import (
    build_conversational_profile_registry,
    choose_profile_interface,
)


def _classification(intent: IntentType) -> IntentClassification:
    return IntentClassification(
        intent=intent,
        confidence=0.9,
        allowed_tool_groups=["memory", "resolution"],
    )


def test_choose_profile_interface_routes_memory_intents_to_memory_expert():
    registry = build_conversational_profile_registry(
        max_steps=15,
        max_tool_calls=20,
        timeout_seconds=120,
    )
    assert (
        choose_profile_interface(_classification(IntentType.MEMORY_SEARCH), registry).name
        == "memory_expert"
    )
    assert (
        choose_profile_interface(_classification(IntentType.DATA_QUERY), registry).name
        == "memory_expert"
    )
    assert (
        choose_profile_interface(_classification(IntentType.CONTACT_LOOKUP), registry).name
        == "memory_expert"
    )


def test_choose_profile_interface_defaults_to_main_for_other_intents():
    registry = build_conversational_profile_registry(
        max_steps=15,
        max_tool_calls=20,
        timeout_seconds=120,
    )
    assert choose_profile_interface(_classification(IntentType.WEB_SEARCH), registry).name == "main"
    assert choose_profile_interface(_classification(IntentType.UNKNOWN), registry).name == "main"
    assert choose_profile_interface(None, registry).name == "main"


def test_profile_registry_includes_memory_expert_and_main():
    registry = build_conversational_profile_registry(
        max_steps=15,
        max_tool_calls=20,
        timeout_seconds=120,
    )
    assert "main" in registry
    assert "memory_expert" in registry
    assert registry["main"].profile.name == "main"
    assert registry["memory_expert"].profile.name == "memory_expert"
    assert registry["memory_expert"].supports_intent(IntentType.MEMORY_SEARCH)
