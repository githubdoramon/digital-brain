"""Shared bounded-runtime profile definitions for orchestrator agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class BoundedRuntimeProfile:
    """Runtime profile describing bounded loop behavior for an agent."""

    name: str
    max_steps: int
    max_tool_calls: int
    timeout_seconds: int
    temperature: float | None = None
    top_p: float | None = None


@dataclass(frozen=True)
class BoundedAgentProfile:
    """Generic bounded-agent profile built on top of runtime settings."""

    name: str
    runtime: BoundedRuntimeProfile
    build_tools_and_handlers: Callable[[], tuple[list[dict[str, Any]], dict[str, Any]]] | None = (
        None
    )
    get_system_prompt: Callable[[], str] | None = None


def build_agent_profile(
    *,
    name: str,
    max_steps: int,
    max_tool_calls: int,
    timeout_seconds: int,
    temperature: float | None = None,
    top_p: float | None = None,
    build_tools_and_handlers: Callable[[], tuple[list[dict[str, Any]], dict[str, Any]]]
    | None = None,
    get_system_prompt: Callable[[], str] | None = None,
) -> BoundedAgentProfile:
    """Create a generic bounded agent profile."""
    runtime = BoundedRuntimeProfile(
        name=name,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        timeout_seconds=timeout_seconds,
        temperature=temperature,
        top_p=top_p,
    )
    return BoundedAgentProfile(
        name=name,
        runtime=runtime,
        build_tools_and_handlers=build_tools_and_handlers,
        get_system_prompt=get_system_prompt,
    )
