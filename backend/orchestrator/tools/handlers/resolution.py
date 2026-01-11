"""
Entity resolution tool handler.

Handles:
- resolve_query: Extract contacts, places, and time ranges from natural language
"""

from time import perf_counter
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agent.state import AgentState


def _log_timing(label: str, start_time: float, **metadata: Any) -> None:
    """Log timing information for performance monitoring."""
    elapsed_ms = (perf_counter() - start_time) * 1000
    parts = [f"[timing] {label}: {elapsed_ms:.1f}ms"]
    if metadata:
        meta_str = ", ".join(f"{k}={v}" for k, v in metadata.items())
        parts.append(f"({meta_str})")
    print(" ".join(parts))


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

    print(f"[tool.resolution] resolve_query(query={query!r})")
    step_start = perf_counter()

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

    _log_timing(
        "tool.resolve_query",
        step_start,
        contacts=len(resolution.get("contacts", [])),
        places=len(resolution.get("places", [])),
    )
    return resolution
