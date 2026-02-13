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
    search_limit: int = 30,
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
    sort_order = args.get("sort_order", "relevance")
    tags = args.get("tags")
    salience_hints = state.get_episodic_hints() if state is not None else []

    search_result = retrieval.search_memories(
        query,
        people=contact_ids,
        time_start=time_start,
        time_end=time_end,
        limit=limit,
        sort_order=sort_order,
        tags=tags,
        salience_hints=salience_hints,
    )
    results = search_result.get("results", [])

    # Update state if provided
    if state is not None:
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

    Retrieves event details by explicit IDs or by time span.
    """
    # Lazy import to avoid circular dependencies
    import events as events_service
    from db import get_conn

    action = str(args.get("action") or "").strip().lower()
    event_ids = args.get("event_ids", [])

    if not action:
        action = "by_ids" if event_ids else "by_time_span"

    events: list[dict[str, Any]] = []
    if action == "by_ids":
        if not event_ids:
            return {
                "error": "event_ids is required when action='by_ids'",
                "events": [],
                "count": 0,
            }
        events = events_service.get_events(event_ids)
    elif action == "by_time_span":
        time_start = str(args.get("time_start") or "").strip()
        time_end = str(args.get("time_end") or "").strip()
        if not time_start or not time_end:
            return {
                "error": "time_start and time_end are required when action='by_time_span'",
                "events": [],
                "count": 0,
            }

        sort_order = str(args.get("sort_order") or "newest").strip().lower()
        order_sql = "e.start_date DESC"
        if sort_order == "oldest":
            order_sql = "e.start_date ASC"
        limit_value = args.get("limit", 50)
        try:
            limit = max(1, min(int(limit_value), 200))
        except (TypeError, ValueError):
            limit = 50

        contact_ids = [
            str(contact_id).strip()
            for contact_id in (args.get("contact_ids") or [])
            if str(contact_id).strip()
        ]

        with get_conn() as conn, conn.cursor() as cur:
            if contact_ids:
                cur.execute(
                    f"""
                    SELECT e.id,
                           e.start_date,
                           e.end_date,
                           e.people,
                           e.tags,
                           e.types,
                           e.title,
                           e.summary,
                           e.external_id,
                           p.place_id, p.name AS place_name, p.city, p.country, p.lat, p.lon
                    FROM events e
                    LEFT JOIN places p ON p.place_id = e.place_id
                    WHERE e.start_date >= %s
                      AND e.start_date <= %s
                      AND e.people && %s
                    ORDER BY {order_sql}
                    LIMIT %s
                    """,
                    (time_start, time_end, contact_ids, limit),
                )
            else:
                cur.execute(
                    f"""
                    SELECT e.id,
                           e.start_date,
                           e.end_date,
                           e.people,
                           e.tags,
                           e.types,
                           e.title,
                           e.summary,
                           e.external_id,
                           p.place_id, p.name AS place_name, p.city, p.country, p.lat, p.lon
                    FROM events e
                    LEFT JOIN places p ON p.place_id = e.place_id
                    WHERE e.start_date >= %s
                      AND e.start_date <= %s
                    ORDER BY {order_sql}
                    LIMIT %s
                    """,
                    (time_start, time_end, limit),
                )
            rows = [dict(row) for row in cur.fetchall()]

        events = [
            {
                "id": row["id"],
                "start_date": row["start_date"].isoformat() if row.get("start_date") else None,
                "end_date": row["end_date"].isoformat() if row.get("end_date") else None,
                "people": row.get("people") or [],
                "tags": row.get("tags") or [],
                "types": row.get("types") or [],
                "title": row.get("title"),
                "summary": row.get("summary"),
                "external_id": row.get("external_id"),
                "place": (
                    {
                        "place_id": row.get("place_id"),
                        "name": row.get("place_name"),
                        "city": row.get("city"),
                        "country": row.get("country"),
                        "lat": row.get("lat"),
                        "lon": row.get("lon"),
                    }
                    if row.get("place_id")
                    else None
                ),
            }
            for row in rows
        ]
    else:
        return {
            "error": "action must be one of: by_ids, by_time_span",
            "events": [],
            "count": 0,
        }

    # Update state if provided
    if state is not None:
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
