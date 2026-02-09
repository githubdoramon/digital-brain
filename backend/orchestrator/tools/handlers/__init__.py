"""
Tool handler implementations.

Each handler module contains functions that execute specific tools.
These are extracted from the original llm_tools.py for better organization.
"""

from .homeassistant import handle_home_assistant
from .memory import handle_get_document, handle_get_events, handle_search_memories
from .resolution import (
    handle_lookup_contact,
    handle_resolve_contacts,
    handle_resolve_query,
)
from .skills import handle_run_skill_script
from .system import handle_bash
from .ui import handle_emit_ui_directive
from .web import handle_fetch_web_page, handle_web_search

# Handler dispatch table
HANDLERS = {
    "search_memories": handle_search_memories,
    "get_events": handle_get_events,
    "get_document": handle_get_document,
    "resolve_query": handle_resolve_query,
    "resolve_contacts": handle_resolve_contacts,
    "lookup_contact": handle_lookup_contact,
    "web_search": handle_web_search,
    "fetch_web_page": handle_fetch_web_page,
    "run_skill_script": handle_run_skill_script,
    "emit_ui_directive": handle_emit_ui_directive,
    "bash": handle_bash,
    "home_assistant": handle_home_assistant,
}


def get_handler(tool_name: str):
    """Get the handler function for a tool."""
    return HANDLERS.get(tool_name)


__all__ = [
    "HANDLERS",
    "get_handler",
    "handle_search_memories",
    "handle_get_events",
    "handle_get_document",
    "handle_resolve_query",
    "handle_resolve_contacts",
    "handle_lookup_contact",
    "handle_web_search",
    "handle_fetch_web_page",
    "handle_run_skill_script",
    "handle_emit_ui_directive",
    "handle_bash",
    "handle_home_assistant",
]
