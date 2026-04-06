"""
Memory-related tool handlers.

Handles:
- search_memories: Semantic search over memories
- get_events: Retrieve event details by ID
- get_document: Retrieve document by ID
- summarize_memories: Bounded recap synthesis across events and documents

Note: Tracing/logging is handled at the controller level via the centralized
trace module. Handlers focus purely on execution logic.
"""

from collections import Counter
from typing import TYPE_CHECKING, Any, Optional

from tools.limit_policy import wants_all_results

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
    if wants_all_results(question):
        limit = None
    else:
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
    from db import enrich_people, fetch_event_people, get_conn, resolve_contact_names

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
        question = str(kwargs.get("question") or "")
        if wants_all_results(question):
            limit: int | None = None
        else:
            limit_value = args.get("limit", 50)
            try:
                limit = max(1, int(limit_value))
            except (TypeError, ValueError):
                limit = 50

        contact_ids = [
            str(contact_id).strip()
            for contact_id in (args.get("contact_ids") or [])
            if str(contact_id).strip()
        ]
        tags = [str(tag).strip() for tag in (args.get("tags") or []) if str(tag).strip()]
        types = [str(value).strip() for value in (args.get("types") or []) if str(value).strip()]

        with get_conn() as conn, conn.cursor() as cur:
            filters: list[str] = ["e.start_date >= %s", "e.start_date <= %s"]
            params: list[Any] = [time_start, time_end]

            if contact_ids:
                filters.append(
                    "EXISTS (SELECT 1 FROM event_contacts ec WHERE ec.event_id = e.id AND ec.contact_id = ANY(%s))"
                )
                params.append(contact_ids)
            if tags:
                filters.append(
                    "EXISTS (SELECT 1 FROM unnest(COALESCE(e.tags, ARRAY[]::text[])) AS tag WHERE lower(tag) = ANY(%s))"
                )
                params.append([tag.lower() for tag in tags])
            if types:
                filters.append(
                    "EXISTS (SELECT 1 FROM unnest(COALESCE(e.types, ARRAY[]::text[])) AS event_type WHERE lower(event_type) = ANY(%s))"
                )
                params.append([value.lower() for value in types])

            base_query = f"""
                    SELECT e.id,
                           e.start_date,
                           e.end_date,
                           e.tags,
                           e.types,
                           e.title,
                           e.summary,
                           e.external_id,
                           p.place_id, p.name AS place_name, p.city, p.country, p.lat, p.lon
                    FROM events e
                    LEFT JOIN places p ON p.place_id = e.place_id
                    WHERE {' AND '.join(filters)}
                    ORDER BY {order_sql}
                    """

            if limit is None:
                cur.execute(base_query, tuple(params))
            else:
                cur.execute(base_query + "\n                    LIMIT %s", (*params, limit))

            rows = [dict(row) for row in cur.fetchall()]

            # Fetch people from junction table + resolve display names.
            event_ids_list = [r["id"] for r in rows]
            people_map = fetch_event_people(cur, event_ids_list)
            all_people: set[str] = set()
            for cids in people_map.values():
                all_people.update(cids)
            contact_names = resolve_contact_names(cur, all_people)
            for r in rows:
                r["people"] = people_map.get(r["id"], [])

        events = [
            {
                "id": row["id"],
                "start_date": row["start_date"].isoformat() if row.get("start_date") else None,
                "end_date": row["end_date"].isoformat() if row.get("end_date") else None,
                "people": enrich_people(row.get("people"), contact_names),
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

    document_id = str(args.get("document_id") or "").strip()

    if not document_id:
        return {"error": "document_id is required"}

    document = documents_service.get_document(document_id)

    if document:
        # Update state if provided
        if state is not None:
            state.add_fact(f"Retrieved document: {document.get('title', document_id)}")
        return {"document": document}

    return {"error": f"Document not found: {document_id}"}


def _truncate_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _normalize_focus(value: Any) -> str:
    normalized = str(value or "summary").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized not in {"summary", "topics", "decisions", "outcomes", "follow_ups"}:
        return "summary"
    return normalized


def _build_document_summary_text(document: dict[str, Any]) -> str:
    raw_metadata = document.get("raw_metadata") or {}
    preview_source = (
        document.get("content")
        or document.get("content_preview")
        or raw_metadata.get("content_english_for_embedding")
        or raw_metadata.get("content")
        or raw_metadata.get("original_content")
        or document.get("description")
        or document.get("snippet")
        or ""
    )
    return str(preview_source or "").strip()


def _format_event_people(people: list[Any]) -> str:
    names: list[str] = []
    for person in people[:6]:
        if isinstance(person, dict):
            label = str(person.get("display_name") or person.get("contact_id") or "").strip()
        else:
            label = str(person or "").strip()
        if label:
            names.append(label)
    return ", ".join(names)


def _append_bounded_lines(
    target: list[str],
    *,
    line: str,
    current_chars: int,
    max_chars: int,
) -> int:
    if not line:
        return current_chars
    proposed = current_chars + len(line) + 1
    if target and proposed > max_chars:
        return current_chars
    target.append(line)
    return proposed


def _build_summary_fallback(
    *,
    focus: str,
    events: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    inspected_documents: list[dict[str, Any]],
) -> str:
    label_map = {
        "summary": "Summary",
        "topics": "Topics",
        "decisions": "Decisions",
        "outcomes": "Outcomes",
        "follow_ups": "Follow-ups",
    }
    lines = [f"{label_map.get(focus, 'Summary')}: reviewed {len(events)} events and {len(documents)} documents."]
    if events:
        titles = [str(event.get("title") or "Untitled event").strip() for event in events[:3]]
        lines.append("Events: " + "; ".join(title for title in titles if title))
    if inspected_documents:
        doc_titles = [str(doc.get("title") or "Untitled document").strip() for doc in inspected_documents[:3]]
        lines.append("Documents: " + "; ".join(title for title in doc_titles if title))

    topic_counter = Counter()
    for item in [*events, *documents]:
        for tag in item.get("tags") or []:
            candidate = str(tag or "").strip()
            if candidate:
                topic_counter[candidate] += 1
    if topic_counter:
        lines.append(
            "Frequent tags: " + ", ".join(tag for tag, _ in topic_counter.most_common(5))
        )
    return "\n".join(lines)


def _synthesize_memory_summary(
    *,
    question: str,
    focus: str,
    tags: list[str],
    event_types: list[str],
    events: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    inspected_documents: list[dict[str, Any]],
) -> str:
    from llm_helpers import call_llm

    evidence_char_budget = 48_000
    current_chars = 0
    event_lines: list[str] = []
    for index, event in enumerate(events, start=1):
        people = _format_event_people(event.get("people") or [])
        parts = [
            f"[{index}] {str(event.get('title') or 'Untitled event').strip()}",
            f"date={event.get('start_date') or 'unknown'}",
        ]
        if event.get("types"):
            parts.append("types=" + ", ".join(str(value) for value in event.get("types") or []))
        if event.get("tags"):
            parts.append("tags=" + ", ".join(str(value) for value in event.get("tags") or []))
        if people:
            parts.append(f"people={people}")
        summary = _truncate_text(event.get("summary"), 4000)
        if summary:
            parts.append(f"summary={summary}")
        current_chars = _append_bounded_lines(
            event_lines,
            line=" | ".join(parts),
            current_chars=current_chars,
            max_chars=evidence_char_budget,
        )
        if current_chars >= evidence_char_budget:
            break

    document_lines: list[str] = []
    for index, document in enumerate(inspected_documents, start=1):
        parts = [
            f"[{index}] {str(document.get('title') or 'Untitled document').strip()}",
            f"date={document.get('document_date') or document.get('created_at') or 'unknown'}",
        ]
        if document.get("tags"):
            parts.append("tags=" + ", ".join(str(value) for value in document.get("tags") or []))
        preview = _truncate_text(_build_document_summary_text(document), 8000)
        if preview:
            parts.append(f"content={preview}")
        current_chars = _append_bounded_lines(
            document_lines,
            line=" | ".join(parts),
            current_chars=current_chars,
            max_chars=evidence_char_budget,
        )
        if current_chars >= evidence_char_budget:
            break

    system_prompt = (
        "You are synthesizing a bounded recap from personal memory evidence. "
        "Use only the provided events and documents. "
        "Documents are equal evidence for outcomes, decisions, and important topics; events are primary for chronology. "
        "Return concise Markdown with these sections when supported by evidence: Overview, Key topics, Outcomes/decisions, Follow-ups. "
        "If evidence is thin, say so explicitly. Do not invent details."
    )
    user_prompt = (
        f"User question: {question.strip()}\n"
        f"Recap focus: {focus}\n"
        f"Domain tags: {', '.join(tags) if tags else 'none'}\n"
        f"Event types: {', '.join(event_types) if event_types else 'none'}\n\n"
        f"Events ({len(events)}):\n" + ("\n".join(event_lines) if event_lines else "None") + "\n\n"
        f"Documents ({len(inspected_documents)} inspected of {len(documents)} matched):\n"
        + ("\n".join(document_lines) if document_lines else "None")
        + (
            "\n\nNote: evidence was budget-bounded after preserving the highest-detail items first."
            if len(event_lines) < len(events) or len(document_lines) < len(inspected_documents)
            else ""
        )
    )
    try:
        return call_llm(
            user_prompt,
            system_prompt=system_prompt,
            timeout=180,
            temperature=0.1,
        ).strip()
    except Exception:
        return _build_summary_fallback(
            focus=focus,
            events=events,
            documents=documents,
            inspected_documents=inspected_documents,
        )


def handle_summarize_memories(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    question: str = "",
    search_limit: int = 30,
    **kwargs,
) -> dict[str, Any]:
    """Execute summarize_memories tool for bounded event+document recaps."""
    import documents as documents_service
    import retrieval

    time_start = str(args.get("time_start") or "").strip()
    time_end = str(args.get("time_end") or "").strip()
    if not time_start or not time_end:
        return {"error": "time_start and time_end are required"}

    focus = _normalize_focus(args.get("query_focus"))
    tags = [str(tag).strip() for tag in (args.get("tags") or []) if str(tag).strip()]
    event_types = [str(value).strip() for value in (args.get("types") or []) if str(value).strip()]
    contact_ids = [
        str(contact_id).strip()
        for contact_id in (args.get("contact_ids") or [])
        if str(contact_id).strip()
    ]

    limit_arg = args.get("limit", search_limit)
    try:
        result_limit = max(1, int(limit_arg))
    except (TypeError, ValueError):
        result_limit = search_limit

    events_result = handle_get_events(
        {
            "action": "by_time_span",
            "time_start": time_start,
            "time_end": time_end,
            "contact_ids": contact_ids,
            "tags": tags,
            "types": event_types,
            "sort_order": "newest",
            "limit": result_limit,
        },
        state=state,
        question=question,
        search_limit=result_limit,
        **kwargs,
    )
    if events_result.get("error"):
        return events_result
    events = [event for event in (events_result.get("events") or []) if isinstance(event, dict)]

    semantic_query = str(question or focus or "").strip()
    if semantic_query.lower() in {"summary", "topics", "decisions", "outcomes", "follow_ups"}:
        semantic_query = ""
    document_search = retrieval.search_memories(
        semantic_query,
        time_start=time_start,
        time_end=time_end,
        tags=tags,
        limit=result_limit,
        sort_order="newest",
    )
    matched_documents = [
        row
        for row in (document_search.get("results") or [])
        if isinstance(row, dict) and str(row.get("kind") or "").strip().lower() == "document"
    ]

    inspected_documents: list[dict[str, Any]] = []
    for row in matched_documents[: min(len(matched_documents), result_limit)]:
        document_id = str(row.get("id") or "").strip()
        if not document_id:
            continue
        document = documents_service.get_document(document_id)
        if isinstance(document, dict) and document:
            inspected_documents.append(document)

    if not events and not matched_documents:
        return {
            "summary": "No matching memories found for that recap window.",
            "focus": focus,
            "events": [],
            "documents": [],
            "inspected_documents": [],
            "count": 0,
            "source_items": [],
        }

    summary = _synthesize_memory_summary(
        question=question,
        focus=focus,
        tags=tags,
        event_types=event_types,
        events=events,
        documents=matched_documents,
        inspected_documents=inspected_documents,
    )

    source_items: list[dict[str, Any]] = []
    for event in events[:5]:
        source_items.append(
            {
                "kind": "event",
                "id": event.get("id"),
                "title": event.get("title"),
                "date": event.get("start_date"),
            }
        )
    for document in inspected_documents[:5]:
        source_items.append(
            {
                "kind": "document",
                "id": document.get("document_id"),
                "title": document.get("title"),
                "date": document.get("document_date") or document.get("created_at"),
            }
        )

    compact_documents = [
        {
            "id": row.get("id"),
            "title": row.get("title"),
            "tags": row.get("tags") or [],
            "document_date": row.get("document_date"),
            "created_at": row.get("created_at"),
            "snippet": row.get("snippet"),
        }
        for row in matched_documents[:result_limit]
    ]

    if state is not None:
        state.add_fact(
            f"Built {focus} recap from {len(events)} events and {len(matched_documents)} documents"
        )

    return {
        "summary": summary,
        "focus": focus,
        "events": events,
        "documents": compact_documents,
        "inspected_documents": [
            {
                "document_id": document.get("document_id"),
                "title": document.get("title"),
                "tags": document.get("tags") or [],
                "document_date": document.get("document_date"),
                "created_at": document.get("created_at"),
                "snippet": _build_document_summary_text(document),
            }
            for document in inspected_documents
        ],
        "count": len(events) + len(matched_documents),
        "source_items": source_items,
    }
