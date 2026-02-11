"""Main bounded-agent profile configuration."""

from __future__ import annotations

from agent.runtime_profiles import BoundedAgentProfile, BoundedRuntimeProfile, build_agent_profile


def build_main_runtime_profile(
    *,
    max_steps: int,
    max_tool_calls: int,
    timeout_seconds: int,
) -> BoundedRuntimeProfile:
    """Create the runtime profile used by the main bounded agent."""
    return build_main_agent_profile(
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        timeout_seconds=timeout_seconds,
    ).runtime


def build_main_agent_profile(
    *,
    max_steps: int,
    max_tool_calls: int,
    timeout_seconds: int,
) -> BoundedAgentProfile:
    """Create main agent profile from generic bounded profile builder."""
    return build_agent_profile(
        name="main",
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        timeout_seconds=timeout_seconds,
    )
