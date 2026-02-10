"""Shared bounded-runtime profile definitions for orchestrator agents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BoundedRuntimeProfile:
    """Runtime profile describing bounded loop behavior for an agent."""

    name: str
    max_steps: int
    max_tool_calls: int
    timeout_seconds: int
    temperature: float | None = None
    top_p: float | None = None


def build_daily_briefing_runtime_profile() -> BoundedRuntimeProfile:
    """Create the runtime profile used by the daily briefing bounded agent."""
    return BoundedRuntimeProfile(
        name="daily_briefing",
        max_steps=8,
        max_tool_calls=12,
        timeout_seconds=180,
        temperature=0.1,
        top_p=None,
    )
