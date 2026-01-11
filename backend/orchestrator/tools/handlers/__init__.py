"""
Tool handler implementations.

Each handler module contains functions that execute specific tools.
These are extracted from the original llm_tools.py for better organization.
"""

from .database import handle_describe_schema, handle_execute_sql
from .homeassistant import handle_home_assistant
from .memory import handle_get_document, handle_get_events, handle_search_memories
from .resolution import handle_resolve_query
from .skills import handle_run_skill_script
from .system import handle_bash
from .web import handle_web_search

# Handler dispatch table
HANDLERS = {
    "search_memories": handle_search_memories,
    "get_events": handle_get_events,
    "get_document": handle_get_document,
    "execute_sql": handle_execute_sql,
    "describe_schema": handle_describe_schema,
    "resolve_query": handle_resolve_query,
    "web_search": handle_web_search,
    "run_skill_script": handle_run_skill_script,
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
    "handle_execute_sql",
    "handle_describe_schema",
    "handle_resolve_query",
    "handle_web_search",
    "handle_run_skill_script",
    "handle_bash",
    "handle_home_assistant",
]
