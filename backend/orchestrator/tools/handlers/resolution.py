"""
Entity resolution tool handler.

Handles:
- resolve_query: Extract contacts, places, and time ranges from natural language

Note: Tracing/logging is handled at the controller level via the centralized
trace module. Handlers focus purely on execution logic.
"""

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agent.state import AgentState


def handle_resolve_query(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    question: str = "",
    **kwargs,
) -> dict[str, Any]:
    """
    Execute resolve_query tool.

    Extracts structured entities (contacts, places, time ranges) from natural language.
    """
    # Lazy import to avoid circular dependencies
    import contacts

    query = args.get("query", question)

    if not query:
        return {"error": "query is required"}

    resolution = contacts.resolve_query(query)

    # Update state if provided
    if state is not None:
        state.resolution = resolution
        contacts_found = len(resolution.get("contacts", []))
        places_found = len(resolution.get("places", []))
        if contacts_found or places_found:
            state.add_fact(
                f"Resolved {contacts_found} contacts and {places_found} places from query"
            )

    return resolution
