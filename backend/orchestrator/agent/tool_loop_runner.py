"""Shared tool-loop runner for bounded profile-based agents."""

from __future__ import annotations

from typing import Any, Callable

from llm_helpers import call_llm_with_tools

from .runtime_profiles import BoundedRuntimeProfile


def run_profiled_tool_loop(
    *,
    prompt: str,
    system_prompt: str,
    tools: list[dict[str, Any]],
    tool_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
    profile: BoundedRuntimeProfile,
) -> dict[str, Any]:
    """Run a bounded tool loop using a shared runtime profile."""
    return call_llm_with_tools(
        prompt,
        tools=tools,
        tool_handlers=tool_handlers,
        system_prompt=system_prompt,
        timeout=profile.timeout_seconds,
        temperature=profile.temperature,
        top_p=profile.top_p,
        max_steps=profile.max_steps,
        max_tool_calls=profile.max_tool_calls,
    )
