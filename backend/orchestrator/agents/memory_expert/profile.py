"""Memory expert bounded-agent profile configuration."""

from __future__ import annotations

from agent.router import IntentType
from agent.runtime_profiles import BoundedAgentProfile, build_agent_profile

SUPPORTED_INTENTS = {
    IntentType.MEMORY_SEARCH,
    IntentType.DATA_QUERY,
    IntentType.CONTACT_LOOKUP,
}


def supports_intent(intent: IntentType) -> bool:
    """Return whether memory expert owns the routed intent."""
    return intent in SUPPORTED_INTENTS


def build_memory_expert_agent_profile(
    *,
    max_steps: int,
    max_tool_calls: int,
    timeout_seconds: int,
) -> BoundedAgentProfile:
    """Create memory expert profile from generic bounded profile builder."""
    return build_agent_profile(
        name="memory_expert",
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        timeout_seconds=timeout_seconds,
    )
