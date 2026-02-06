"""
Memory-related tool handlers.

Handles:
- search_memories: Semantic search over memories
- get_events: Retrieve event details by ID
- get_document: Retrieve document by ID

Note: Tracing/logging is handled at the controller level via the centralized
trace module. Handlers focus purely on execution logic.
"""

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agent.state import AgentState


def handle_search_memories(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    question: str = "",
    search_limit: int = 5,
    **kwargs,
) -> dict[str, Any]:
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

    return {"results": results, "count": len(results)}


def handle_get_events(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> dict[str, Any]:
    """
    Execute get_events tool.

    Retrieves full event details by IDs.
    """
    # Lazy import to avoid circular dependencies
    import events as events_service

    event_ids = args.get("event_ids", [])

    if not event_ids:
        return {"error": "event_ids is required", "events": [], "count": 0}

    events = events_service.get_events(event_ids) if event_ids else []

    # Update state if provided
    if state is not None:
        state.detailed_events.extend(events)
        if events:
            state.add_fact(f"Retrieved {len(events)} event details")

    return {"events": events, "count": len(events)}


def handle_get_document(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> dict[str, Any]:
    """
    Execute get_document tool.

    Retrieves full document content by ID.
    """
    # Lazy import to avoid circular dependencies
    import documents as documents_service

    document_id = args.get("document_id")

    if not document_id:
        return {"error": "document_id is required"}

    document = documents_service.get_document(document_id)

    if document:
        # Update state if provided
        if state is not None:
            state.add_fact(f"Retrieved document: {document.get('title', document_id)}")
        return {"document": document}

    return {"error": f"Document not found: {document_id}"}
