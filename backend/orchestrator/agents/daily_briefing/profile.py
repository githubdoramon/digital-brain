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

# Per-event research uses only web tools with tight limits.
EVENT_RESEARCH_ALLOWED_TOOLS = (
    "web_search",
    "fetch_web_page",
)


def get_daily_briefing_profile() -> BoundedRuntimeProfile:
    """Return the runtime profile for daily briefing generation."""
    return build_daily_briefing_agent_profile().runtime


# -- Main briefing assembly profile ------------------------------------------


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
    return (
        "You are a personal daily briefing writer. You produce concise, actionable prep "
        "documents in Markdown.\n"
        "\n"
        "HARD RULES:\n"
        "- Write in direct, practical tone. Use future tense for upcoming events.\n"
        "- NEVER use meta-commentary about the input data (e.g. 'the text includes', "
        "'there are several articles', 'it appears', 'you provided').\n"
        "- NEVER produce generic category lists in place of concrete content. Every bullet "
        "must contain a specific fact, title, action item, or recommendation.\n"
        "- NEVER ask the user questions or offer to do more ('let me know', 'if you'd like').\n"
        "- NEVER describe what the data contains — transform it into a briefing.\n"
        "- If a section has no relevant content, write a single short note (e.g. "
        "'No notable news today.') and move on.\n"
        "- Output Markdown only. No preamble, no sign-off."
    )


# -- Per-event research profile ----------------------------------------------


def build_event_research_profile() -> BoundedAgentProfile:
    """Lightweight profile for per-event web research.

    Each event gets at most 3 steps / 4 tool calls (one search + a couple of
    page fetches) with a 60-second timeout so we don't block the pipeline.
    """
    return build_agent_profile(
        name="daily_briefing_event_research",
        max_steps=3,
        max_tool_calls=4,
        timeout_seconds=60,
        temperature=0.1,
        top_p=None,
        build_tools_and_handlers=build_event_research_tools_and_handlers,
        get_system_prompt=get_event_research_system_prompt,
    )


def build_event_research_tools_and_handlers() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Web-only tools for per-event research."""
    registry = get_registry()
    tools = registry.get_tool_definitions(list(EVENT_RESEARCH_ALLOWED_TOOLS))
    tool_handlers = {
        "web_search": lambda args: handle_web_search(args),
        "fetch_web_page": lambda args: handle_fetch_web_page(args),
    }
    return tools, tool_handlers


def get_event_research_system_prompt() -> str:
    """System prompt for per-event research step."""
    return (
        "You are a research assistant preparing context for a calendar event. "
        "Use tools ONLY when the event would clearly benefit from external context "
        "(e.g. a meeting with a company you could look up, a conference with a public "
        "agenda, a restaurant you could check). If the event is routine or internal "
        "with no obvious research angle, skip tool use and respond directly."
    )
