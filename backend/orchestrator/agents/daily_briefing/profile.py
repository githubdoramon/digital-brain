"""Daily briefing bounded profile and tool policy."""

from __future__ import annotations

from typing import Any

from agent.runtime_profiles import (
    BoundedAgentProfile,
    BoundedRuntimeProfile,
    build_agent_profile,
)
from tools.handlers.memory import handle_get_document, handle_search_memories
from tools.handlers.web import handle_fetch_web_page, handle_web_search
from tools.registry import get_registry

TOOL_SEARCH_LIMIT = 8
DAILY_BRIEFING_ALLOWED_TOOLS = (
    "search_memories",
    "get_document",
    "web_search",
    "fetch_web_page",
)


def get_daily_briefing_profile() -> BoundedRuntimeProfile:
    """Return the runtime profile for daily briefing generation."""
    return build_daily_briefing_agent_profile().runtime


def build_daily_briefing_agent_profile() -> BoundedAgentProfile:
    """Build daily briefing agent profile from generic profile constructor."""
    return build_agent_profile(
        name="daily_briefing",
        max_steps=8,
        max_tool_calls=12,
        timeout_seconds=180,
        temperature=0.1,
        top_p=None,
        build_tools_and_handlers=build_daily_briefing_tools_and_handlers,
        get_system_prompt=get_daily_briefing_system_prompt,
    )


def build_daily_briefing_tools_and_handlers() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build bounded tool visibility and handlers for daily briefing."""
    registry = get_registry()
    tools = registry.get_tool_definitions(list(DAILY_BRIEFING_ALLOWED_TOOLS))
    tool_handlers = {
        "search_memories": lambda args: handle_search_memories(
            args,
            question="daily briefing",
            search_limit=TOOL_SEARCH_LIMIT,
        ),
        "get_document": lambda args: handle_get_document(args),
        "web_search": lambda args: handle_web_search(args),
        "fetch_web_page": lambda args: handle_fetch_web_page(args),
    }
    return tools, tool_handlers


def get_daily_briefing_system_prompt() -> str:
    """System prompt for daily briefing writing behavior."""
    return "You are a precise writing engine. Follow the user instructions exactly."
