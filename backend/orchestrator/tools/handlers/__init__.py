"""
Tool handler implementations.

Each handler module contains functions that execute specific tools.
These are extracted from the original llm_tools.py for better organization.
"""

from .graph import handle_query_graph
from .homeassistant import handle_home_assistant
from .memory import (
    handle_get_conversation,
    handle_get_document,
    handle_get_events,
    handle_search_memories,
    handle_summarize_memories,
)
from .pdf import handle_create_pdf, handle_ingest_generated_pdf
from .resolution import (
    handle_lookup_contact,
    handle_lookup_contact_places,
    handle_lookup_place_contacts,
    handle_lookup_places,
    handle_resolve_contacts,
    handle_select_contacts,
)
from .skills import handle_run_skill_script
from .system import handle_bash
from .ui import handle_emit_ui_directive
from .visual import handle_visual_inspect
from .web import handle_fetch_web_page, handle_web_search

# Handler dispatch table
HANDLERS = {
    "search_memories": handle_search_memories,
    "get_conversation": handle_get_conversation,
    "get_events": handle_get_events,
    "get_document": handle_get_document,
    "summarize_memories": handle_summarize_memories,
    "query_graph": handle_query_graph,
    "resolve_contacts": handle_resolve_contacts,
    "lookup_contact": handle_lookup_contact,
    "select_contacts": handle_select_contacts,
    "lookup_places": handle_lookup_places,
    "lookup_contact_places": handle_lookup_contact_places,
    "lookup_place_contacts": handle_lookup_place_contacts,
    "web_search": handle_web_search,
    "fetch_web_page": handle_fetch_web_page,
    "visual_inspect": handle_visual_inspect,
    "run_skill_script": handle_run_skill_script,
    "create_pdf": handle_create_pdf,
    "ingest_generated_pdf": handle_ingest_generated_pdf,
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
    "handle_bash",
    "handle_create_pdf",
    "handle_emit_ui_directive",
    "handle_fetch_web_page",
    "handle_get_document",
    "handle_get_events",
    "handle_home_assistant",
    "handle_ingest_generated_pdf",
    "handle_lookup_contact",
    "handle_lookup_contact_places",
    "handle_lookup_place_contacts",
    "handle_lookup_places",
    "handle_query_graph",
    "handle_resolve_contacts",
    "handle_run_skill_script",
    "handle_search_memories",
    "handle_select_contacts",
    "handle_summarize_memories",
    "handle_visual_inspect",
    "handle_web_search",
]
