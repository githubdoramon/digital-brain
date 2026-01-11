"""
Memory-related tool handlers.

Handles:
- search_memories: Semantic search over memories
- get_events: Retrieve event details by ID
- get_document: Retrieve document by ID
"""

from time import perf_counter
from typing import Any, Dict, List, Optional, TYPE_CHECKING

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


def handle_search_memories(
    args: Dict[str, Any],
    state: Optional["AgentState"] = None,
    question: str = "",
    search_limit: int = 5,
) -> Dict[str, Any]:
    """
    Execute search_memories tool.

    Performs semantic (vector) search over user's memories.
    """
    # Lazy import to avoid circular dependencies
    import retrieval

    query = args.get("query", question)
    limit = args.get("limit", search_limit)
    time_start = args.get("time_start")
    time_end = args.get("time_end")
    contact_ids = args.get("contact_ids")  # Maps to 'people' parameter
    # Note: 'tags' from tool schema is not yet supported by retrieval.search_memories

    print(
        f"[tool.memory] search_memories(query={query!r}, limit={limit}, "
        f"time_start={time_start}, time_end={time_end}, contact_ids={contact_ids})"
    )
    step_start = perf_counter()

    search_result = retrieval.search_memories(
        query,
        people=contact_ids,
        time_start=time_start,
        time_end=time_end,
        limit=limit,
    )
    results = search_result.get("results", [])

    # Update state if provided
    if state is not None:
        state.search_results.extend(results)
        if results:
            state.add_fact(f"Found {len(results)} memories matching '{query}'")

    _log_timing("tool.search_memories", step_start, results=len(results))
    return {"results": results, "count": len(results)}


def handle_get_events(
    args: Dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Execute get_events tool.

    Retrieves full event details by IDs.
    """
    # Lazy import to avoid circular dependencies
    import events as events_service

    event_ids = args.get("event_ids", [])

    if not event_ids:
        return {"error": "event_ids is required", "events": [], "count": 0}

    print(f"[tool.memory] get_events(event_ids={event_ids})")
    step_start = perf_counter()

    events = events_service.get_events(event_ids) if event_ids else []

    # Update state if provided
    if state is not None:
        state.detailed_events.extend(events)
        if events:
            state.add_fact(f"Retrieved {len(events)} event details")

    _log_timing(
        "tool.get_events", step_start, found=len(events), requested=len(event_ids)
    )
    return {"events": events, "count": len(events)}


def handle_get_document(
    args: Dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Execute get_document tool.

    Retrieves full document content by ID.
    """
    # Lazy import to avoid circular dependencies
    import documents as documents_service

    document_id = args.get("document_id")

    if not document_id:
        return {"error": "document_id is required"}

    print(f"[tool.memory] get_document(document_id={document_id!r})")
    step_start = perf_counter()

    document = documents_service.get_document(document_id)

    _log_timing("tool.get_document", step_start, found=document is not None)

    if document:
        # Update state if provided
        if state is not None:
            state.add_fact(f"Retrieved document: {document.get('title', document_id)}")
        return {"document": document}

    return {"error": f"Document not found: {document_id}"}
