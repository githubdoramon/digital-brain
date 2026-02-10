"""Main bounded-agent profile configuration."""

from __future__ import annotations

from agent.runtime_profiles import BoundedRuntimeProfile


def build_main_runtime_profile(
    *,
    max_steps: int,
    max_tool_calls: int,
    timeout_seconds: int,
) -> BoundedRuntimeProfile:
    """Create the runtime profile used by the main bounded agent."""
    return BoundedRuntimeProfile(
        name="main",
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        timeout_seconds=timeout_seconds,
        temperature=None,
        top_p=None,
    )
