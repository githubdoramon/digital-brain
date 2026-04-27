"""
Structured query handlers for the personal memory graph.

Provides deterministic counts and groupings over events, contacts, places,
and documents — distinct from the semantic retrieval surface in `memory.py`.
Filter parameters are shared across entities; each entity interprets the
filters that make sense for its schema and ignores the rest.
"""

from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from agent.state import AgentState


_ENTITY_DISTINCT: dict[str, set[str]] = {
    "events": {"events", "contacts", "places"},
    "contacts": {"contacts", "places"},
    "places": {"places", "events", "contacts"},
    "documents": {"documents"},
    "todos": {"todos"},
}

_ENTITY_GROUP_BY: dict[str, set[str]] = {
    "events": {"type", "month", "week", "day", "place", "tag", "contact"},
    "contacts": {"tag", "place"},
    "places": {"city", "country"},
    "documents": {"tag", "month", "week", "day", "file_mime"},
    "todos": {"status", "month", "week", "day", "contact"},
}

_SUPPORTED_ENTITIES = set(_ENTITY_DISTINCT.keys())
_SUPPORTED_OPERATIONS = {"count", "group_by"}

_TODO_TIME_COLUMNS = {
    "due": "t.due_date",
    "updated": "t.updated_at",
    "created": "t.created_at",
}
_TODO_STATUS_VALUES = {"pending", "completed"}


def _normalize_str_list(value: Any) -> list[str]:
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def _where_clause(filters: list[str]) -> str:
    return f"WHERE {' AND '.join(filters)}" if filters else ""


# -------------------------------------------------------------------------
# Per-entity filter builders. Each returns (sql_fragments, params, applied)
# where `applied` is the echo-back of filters that took effect.
# -------------------------------------------------------------------------


def _build_event_filters(args: dict[str, Any]) -> tuple[list[str], list[Any], dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    applied: dict[str, Any] = {}

    time_start = str(args.get("time_start") or "").strip()
    time_end = str(args.get("time_end") or "").strip()
    contact_ids = _normalize_str_list(args.get("contact_ids"))
    place_ids = _normalize_str_list(args.get("place_ids"))
    tags = [t.lower() for t in _normalize_str_list(args.get("tags"))]
    types = [t.lower() for t in _normalize_str_list(args.get("types"))]

    if time_start:
        filters.append("e.start_date >= %s")
        params.append(time_start)
        applied["time_start"] = time_start
    if time_end:
        filters.append("e.start_date <= %s")
        params.append(time_end)
        applied["time_end"] = time_end
    if contact_ids:
        filters.append(
            "EXISTS (SELECT 1 FROM event_contacts ec WHERE ec.event_id = e.id AND ec.contact_id = ANY(%s))"
        )
        params.append(contact_ids)
        applied["contact_ids"] = contact_ids
    if place_ids:
        filters.append("e.place_id = ANY(%s)")
        params.append(place_ids)
        applied["place_ids"] = place_ids
    if tags:
        filters.append(
            "EXISTS (SELECT 1 FROM unnest(COALESCE(e.tags, ARRAY[]::text[])) AS tag WHERE lower(tag) = ANY(%s))"
        )
        params.append(tags)
        applied["tags"] = tags
    if types:
        filters.append(
            "EXISTS (SELECT 1 FROM unnest(COALESCE(e.types, ARRAY[]::text[])) AS event_type WHERE lower(event_type) = ANY(%s))"
        )
        params.append(types)
        applied["types"] = types

    return filters, params, applied


def _build_contact_filters(args: dict[str, Any]) -> tuple[list[str], list[Any], dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    applied: dict[str, Any] = {}

    contact_ids = _normalize_str_list(args.get("contact_ids"))
    place_ids = _normalize_str_list(args.get("place_ids"))
    tags = [t.lower() for t in _normalize_str_list(args.get("tags"))]

    if contact_ids:
        filters.append("c.contact_id = ANY(%s)")
        params.append(contact_ids)
        applied["contact_ids"] = contact_ids
    if place_ids:
        filters.append(
            "EXISTS (SELECT 1 FROM contact_places cp WHERE cp.contact_id = c.contact_id AND cp.place_id = ANY(%s))"
        )
        params.append(place_ids)
        applied["place_ids"] = place_ids
    if tags:
        filters.append(
            "EXISTS (SELECT 1 FROM unnest(COALESCE(c.tags, ARRAY[]::text[])) AS tag WHERE lower(tag) = ANY(%s))"
        )
        params.append(tags)
        applied["tags"] = tags

    return filters, params, applied


def _build_place_filters(args: dict[str, Any]) -> tuple[list[str], list[Any], dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    applied: dict[str, Any] = {}

    place_ids = _normalize_str_list(args.get("place_ids"))
    contact_ids = _normalize_str_list(args.get("contact_ids"))

    if place_ids:
        filters.append("p.place_id = ANY(%s)")
        params.append(place_ids)
        applied["place_ids"] = place_ids
    if contact_ids:
        filters.append(
            "EXISTS (SELECT 1 FROM contact_places cp WHERE cp.place_id = p.place_id AND cp.contact_id = ANY(%s))"
        )
        params.append(contact_ids)
        applied["contact_ids"] = contact_ids

    return filters, params, applied


def _build_document_filters(args: dict[str, Any]) -> tuple[list[str], list[Any], dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    applied: dict[str, Any] = {}

    time_start = str(args.get("time_start") or "").strip()
    time_end = str(args.get("time_end") or "").strip()
    tags = [t.lower() for t in _normalize_str_list(args.get("tags"))]

    if time_start:
        filters.append("d.document_date >= %s")
        params.append(time_start)
        applied["time_start"] = time_start
    if time_end:
        filters.append("d.document_date <= %s")
        params.append(time_end)
        applied["time_end"] = time_end
    if tags:
        filters.append(
            "EXISTS (SELECT 1 FROM unnest(COALESCE(d.tags, ARRAY[]::text[])) AS tag WHERE lower(tag) = ANY(%s))"
        )
        params.append(tags)
        applied["tags"] = tags

    return filters, params, applied


def _resolve_todo_time_field(value: Any) -> str:
    candidate = str(value or "due").strip().lower()
    return candidate if candidate in _TODO_TIME_COLUMNS else "due"


def _build_todo_filters(args: dict[str, Any]) -> tuple[list[str], list[Any], dict[str, Any]]:
    from todos import _status_sql_expression, _status_sql_params

    filters: list[str] = []
    params: list[Any] = []
    applied: dict[str, Any] = {}

    time_start = str(args.get("time_start") or "").strip()
    time_end = str(args.get("time_end") or "").strip()
    time_field = _resolve_todo_time_field(args.get("time_field"))
    time_col = _TODO_TIME_COLUMNS[time_field]

    if time_start:
        filters.append(f"{time_col} >= %s")
        params.append(time_start)
        applied["time_start"] = time_start
    if time_end:
        filters.append(f"{time_col} <= %s")
        params.append(time_end)
        applied["time_end"] = time_end
    if time_start or time_end:
        applied["time_field"] = time_field

    status = str(args.get("status") or "").strip().lower()
    if status in _TODO_STATUS_VALUES:
        filters.append(f"({_status_sql_expression('t.status')}) = %s")
        params.extend([*_status_sql_params(), status])
        applied["status"] = status

    contact_ids = _normalize_str_list(args.get("contact_ids"))
    event_ids = _normalize_str_list(args.get("event_ids"))
    place_ids = _normalize_str_list(args.get("place_ids"))

    if contact_ids:
        filters.append(
            "EXISTS (SELECT 1 FROM todo_contacts tc WHERE tc.todo_id = t.todo_id AND tc.contact_id = ANY(%s))"
        )
        params.append(contact_ids)
        applied["contact_ids"] = contact_ids
    if event_ids:
        filters.append(
            "EXISTS (SELECT 1 FROM todo_events tev WHERE tev.todo_id = t.todo_id AND tev.event_id = ANY(%s))"
        )
        params.append(event_ids)
        applied["event_ids"] = event_ids
    if place_ids:
        filters.append(
            "EXISTS (SELECT 1 FROM todo_places tp WHERE tp.todo_id = t.todo_id AND tp.place_id = ANY(%s))"
        )
        params.append(place_ids)
        applied["place_ids"] = place_ids

    return filters, params, applied


_FILTER_BUILDERS: dict[str, Callable[[dict[str, Any]], tuple[list[str], list[Any], dict[str, Any]]]] = {
    "events": _build_event_filters,
    "contacts": _build_contact_filters,
    "places": _build_place_filters,
    "documents": _build_document_filters,
    "todos": _build_todo_filters,
}


# -------------------------------------------------------------------------
# Count operation — entity x distinct dispatches to the right SQL.
# -------------------------------------------------------------------------


def _count_query_events(filters: list[str], distinct: str) -> tuple[str, list[str]]:
    if distinct == "events":
        return f"SELECT COUNT(DISTINCT e.id) AS total FROM events e {_where_clause(filters)}", filters
    if distinct == "contacts":
        return (
            "SELECT COUNT(DISTINCT ec.contact_id) AS total "
            "FROM events e JOIN event_contacts ec ON ec.event_id = e.id "
            f"{_where_clause(filters)}"
        ), filters
    # places
    place_filters = [*filters, "e.place_id IS NOT NULL"]
    return (
        f"SELECT COUNT(DISTINCT e.place_id) AS total FROM events e {_where_clause(place_filters)}"
    ), place_filters


def _count_query_contacts(filters: list[str], distinct: str) -> tuple[str, list[str]]:
    if distinct == "contacts":
        return (
            f"SELECT COUNT(DISTINCT c.contact_id) AS total FROM contacts c {_where_clause(filters)}",
            filters,
        )
    # places
    return (
        "SELECT COUNT(DISTINCT cp.place_id) AS total "
        "FROM contacts c JOIN contact_places cp ON cp.contact_id = c.contact_id "
        f"{_where_clause(filters)}"
    ), filters


def _count_query_places(filters: list[str], distinct: str) -> tuple[str, list[str]]:
    if distinct == "places":
        return (
            f"SELECT COUNT(DISTINCT p.place_id) AS total FROM places p {_where_clause(filters)}",
            filters,
        )
    if distinct == "contacts":
        return (
            "SELECT COUNT(DISTINCT cp.contact_id) AS total "
            "FROM places p JOIN contact_places cp ON cp.place_id = p.place_id "
            f"{_where_clause(filters)}"
        ), filters
    # events
    return (
        "SELECT COUNT(DISTINCT e.id) AS total "
        "FROM places p JOIN events e ON e.place_id = p.place_id "
        f"{_where_clause(filters)}"
    ), filters


def _count_query_documents(filters: list[str], _distinct: str) -> tuple[str, list[str]]:
    return (
        f"SELECT COUNT(DISTINCT d.document_id) AS total FROM documents d {_where_clause(filters)}",
        filters,
    )


def _count_query_todos(filters: list[str], _distinct: str) -> tuple[str, list[str]]:
    return (
        f"SELECT COUNT(DISTINCT t.todo_id) AS total FROM todos t {_where_clause(filters)}",
        filters,
    )


_COUNT_QUERY_BUILDERS = {
    "events": _count_query_events,
    "contacts": _count_query_contacts,
    "places": _count_query_places,
    "documents": _count_query_documents,
    "todos": _count_query_todos,
}


def _run_count(entity: str, args: dict[str, Any]) -> dict[str, Any]:
    from db import get_conn

    distinct = str(args.get("distinct") or entity).strip().lower()
    if distinct not in _ENTITY_DISTINCT[entity]:
        return {
            "error": f"distinct for entity={entity!r} must be one of: {sorted(_ENTITY_DISTINCT[entity])}",
            "operation": "count",
            "entity": entity,
        }

    filters, params, applied = _FILTER_BUILDERS[entity](args)
    query, _ = _COUNT_QUERY_BUILDERS[entity](filters, distinct)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, tuple(params))
        row = cur.fetchone()
        count = int(row["total"]) if row and row.get("total") is not None else 0

    return {
        "operation": "count",
        "entity": entity,
        "distinct": distinct,
        "count": count,
        "filters": applied,
    }


# -------------------------------------------------------------------------
# Group-by operation — per-entity dimension table.
# Each entry: (from_clause, key_expr, primary_id_col, extra_filter_or_none)
# -------------------------------------------------------------------------


_GROUP_BY_SQL: dict[str, dict[str, tuple[str, str, str, Optional[str]]]] = {
    "events": {
        "type": (
            "FROM events e, unnest(COALESCE(e.types, ARRAY[]::text[])) AS type_value",
            "type_value",
            "e.id",
            None,
        ),
        "tag": (
            "FROM events e, unnest(COALESCE(e.tags, ARRAY[]::text[])) AS tag_value",
            "tag_value",
            "e.id",
            None,
        ),
        "month": ("FROM events e", "to_char(e.start_date, 'YYYY-MM')", "e.id", None),
        "week": (
            "FROM events e",
            "to_char(date_trunc('week', e.start_date), 'IYYY-\"W\"IW')",
            "e.id",
            None,
        ),
        "day": ("FROM events e", "to_char(e.start_date, 'YYYY-MM-DD')", "e.id", None),
        "place": ("FROM events e", "e.place_id", "e.id", "e.place_id IS NOT NULL"),
        "contact": (
            "FROM events e JOIN event_contacts ec ON ec.event_id = e.id",
            "ec.contact_id",
            "e.id",
            None,
        ),
    },
    "contacts": {
        "tag": (
            "FROM contacts c, unnest(COALESCE(c.tags, ARRAY[]::text[])) AS tag_value",
            "tag_value",
            "c.contact_id",
            None,
        ),
        "place": (
            "FROM contacts c JOIN contact_places cp ON cp.contact_id = c.contact_id",
            "cp.place_id",
            "c.contact_id",
            None,
        ),
    },
    "places": {
        "city": ("FROM places p", "p.city", "p.place_id", None),
        "country": ("FROM places p", "p.country", "p.place_id", None),
    },
    "documents": {
        "tag": (
            "FROM documents d, unnest(COALESCE(d.tags, ARRAY[]::text[])) AS tag_value",
            "tag_value",
            "d.document_id",
            None,
        ),
        "month": ("FROM documents d", "to_char(d.document_date, 'YYYY-MM')", "d.document_id", None),
        "week": (
            "FROM documents d",
            "to_char(date_trunc('week', d.document_date), 'IYYY-\"W\"IW')",
            "d.document_id",
            None,
        ),
        "day": (
            "FROM documents d",
            "to_char(d.document_date, 'YYYY-MM-DD')",
            "d.document_id",
            None,
        ),
        "file_mime": ("FROM documents d", "d.file_mime", "d.document_id", None),
    },
}


def _resolve_group_keys(cur, group_by: str, rows: list[tuple[Any, int]]) -> list[dict[str, Any]]:
    """Hydrate ID-shaped keys (place/contact) into display labels."""
    from db import resolve_contact_names

    if group_by == "place":
        place_ids = [key for key, _ in rows if key]
        place_names: dict[str, str] = {}
        if place_ids:
            cur.execute(
                "SELECT place_id, name FROM places WHERE place_id = ANY(%s)",
                (place_ids,),
            )
            place_names = {
                row["place_id"]: row["name"]
                for row in cur.fetchall()
                if row.get("name")
            }
        return [
            {"key": place_names.get(key, key), "place_id": key, "count": count}
            for key, count in rows
        ]
    if group_by == "contact":
        contact_ids = [key for key, _ in rows if key]
        display_names = (
            resolve_contact_names(cur, set(contact_ids)) if contact_ids else {}
        )
        return [
            {"key": display_names.get(key, key), "contact_id": key, "count": count}
            for key, count in rows
        ]
    return [{"key": key, "count": count} for key, count in rows]


def _run_group_by(entity: str, args: dict[str, Any]) -> dict[str, Any]:
    from db import get_conn

    group_by = str(args.get("group_by") or "").strip().lower()
    if group_by not in _ENTITY_GROUP_BY[entity]:
        return {
            "error": f"group_by for entity={entity!r} must be one of: {sorted(_ENTITY_GROUP_BY[entity])}",
            "operation": "group_by",
            "entity": entity,
        }

    try:
        limit = max(1, min(int(args.get("limit", 50)), 200))
    except (TypeError, ValueError):
        limit = 50

    if entity == "todos":
        return _run_group_by_todos(args, group_by, limit)

    filters, params, applied = _FILTER_BUILDERS[entity](args)
    from_clause, key_expr, primary_id_col, extra_filter = _GROUP_BY_SQL[entity][group_by]
    effective_filters = list(filters)
    if extra_filter:
        effective_filters.append(extra_filter)

    query = (
        f"SELECT {key_expr} AS bucket_key, COUNT(DISTINCT {primary_id_col}) AS bucket_count "
        f"{from_clause} "
        f"{_where_clause(effective_filters)} "
        f"GROUP BY {key_expr} "
        f"ORDER BY bucket_count DESC, bucket_key ASC "
        f"LIMIT %s"
    )

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, (*params, limit))
        rows = [
            (row["bucket_key"], int(row["bucket_count"]))
            for row in cur.fetchall()
        ]
        groups = _resolve_group_keys(cur, group_by, rows)

    return {
        "operation": "group_by",
        "entity": entity,
        "group_by": group_by,
        "groups": groups,
        "filters": applied,
    }


def _run_group_by_todos(args: dict[str, Any], group_by: str, limit: int) -> dict[str, Any]:
    from db import get_conn
    from todos import _status_sql_expression, _status_sql_params

    filters, params, applied = _build_todo_filters(args)
    select_params: tuple = ()
    from_clause = "FROM todos t"

    if group_by == "status":
        key_expr = f"({_status_sql_expression('t.status')})"
        select_params = _status_sql_params()
    elif group_by in {"month", "week", "day"}:
        time_field = _resolve_todo_time_field(args.get("time_field"))
        time_col = _TODO_TIME_COLUMNS[time_field]
        applied.setdefault("time_field", time_field)
        if group_by == "month":
            key_expr = f"to_char({time_col}, 'YYYY-MM')"
        elif group_by == "week":
            key_expr = f"to_char(date_trunc('week', {time_col}), 'IYYY-\"W\"IW')"
        else:
            key_expr = f"to_char({time_col}, 'YYYY-MM-DD')"
    else:  # contact
        key_expr = "tc.contact_id"
        from_clause = "FROM todos t JOIN todo_contacts tc ON tc.todo_id = t.todo_id"

    query = (
        f"SELECT {key_expr} AS bucket_key, COUNT(DISTINCT t.todo_id) AS bucket_count "
        f"{from_clause} "
        f"{_where_clause(filters)} "
        f"GROUP BY {key_expr} "
        f"ORDER BY bucket_count DESC, bucket_key ASC "
        f"LIMIT %s"
    )

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, (*select_params, *params, limit))
        rows = [
            (row["bucket_key"], int(row["bucket_count"]))
            for row in cur.fetchall()
        ]
        groups = _resolve_group_keys(cur, group_by, rows)

    return {
        "operation": "group_by",
        "entity": "todos",
        "group_by": group_by,
        "groups": groups,
        "filters": applied,
    }


# -------------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------------


def handle_query_graph(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> dict[str, Any]:
    """Execute structured count/group_by queries over the memory graph."""
    entity = str(args.get("entity") or "events").strip().lower()
    if entity not in _SUPPORTED_ENTITIES:
        return {
            "error": f"entity must be one of: {sorted(_SUPPORTED_ENTITIES)}",
        }

    operation = str(args.get("operation") or "").strip().lower()
    if operation not in _SUPPORTED_OPERATIONS:
        return {
            "error": f"operation must be one of: {sorted(_SUPPORTED_OPERATIONS)}",
            "entity": entity,
        }

    result = _run_count(entity, args) if operation == "count" else _run_group_by(entity, args)

    if state is not None and not result.get("error"):
        if operation == "count":
            state.add_fact(
                f"query_graph[{entity}] count[{result.get('distinct')}]={result.get('count')}"
            )
        else:
            state.add_fact(
                f"query_graph[{entity}] group_by[{result.get('group_by')}] returned "
                f"{len(result.get('groups') or [])} buckets"
            )

    return result
