"""Tests for the query_graph handler.

Stubs `db.get_conn` so tests exercise the SQL-building logic without a real
Postgres connection. The fake cursor records executed queries for assertions
and serves canned results from a queue.
"""

import sys
from types import SimpleNamespace

import pytest

from tools.handlers.graph import handle_query_graph


class _Cursor:
    def __init__(self, results=None):
        self.executed: list[tuple[str, tuple]] = []
        self._results = list(results or [])

    def execute(self, query, params):
        self.executed.append((query, params))

    def fetchone(self):
        return self._results.pop(0) if self._results else None

    def fetchall(self):
        return self._results.pop(0) if self._results else []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _install_fake_db(monkeypatch, cursor, *, resolve_contact_names=None):
    fake_db = SimpleNamespace(
        get_conn=lambda: _Conn(cursor),
        resolve_contact_names=resolve_contact_names or (lambda cur, ids: {}),
    )
    monkeypatch.setitem(sys.modules, "db", fake_db)


_FAKE_STATUS_SQL = (
    "CASE "
    "WHEN lower(coalesce({col}, '')) = %s THEN %s "
    "WHEN lower(coalesce({col}, '')) = ANY(%s) THEN %s "
    "ELSE %s END"
)
_FAKE_STATUS_PARAMS = (
    "completed",
    "completed",
    ["complete", "done", "accomplished", "closed"],
    "completed",
    "pending",
)


def _install_fake_todos(monkeypatch):
    fake_todos = SimpleNamespace(
        _status_sql_expression=lambda col: _FAKE_STATUS_SQL.format(col=col),
        _status_sql_params=lambda: _FAKE_STATUS_PARAMS,
    )
    monkeypatch.setitem(sys.modules, "todos", fake_todos)


def test_unknown_entity_returns_error():
    result = handle_query_graph({"entity": "people", "operation": "count"})
    assert "error" in result
    assert "entity" in result["error"]


def test_unknown_operation_returns_error():
    result = handle_query_graph({"operation": "describe"})
    assert "error" in result
    assert "operation" in result["error"]


def test_count_unknown_distinct_returns_error(monkeypatch):
    cursor = _Cursor()
    _install_fake_db(monkeypatch, cursor)
    result = handle_query_graph({"operation": "count", "distinct": "tags"})
    assert "error" in result
    assert result["operation"] == "count"
    assert cursor.executed == []


def test_group_by_missing_dimension_returns_error(monkeypatch):
    cursor = _Cursor()
    _install_fake_db(monkeypatch, cursor)
    result = handle_query_graph({"operation": "group_by"})
    assert "error" in result
    assert result["operation"] == "group_by"
    assert cursor.executed == []


def test_count_events_with_time_window(monkeypatch):
    cursor = _Cursor(results=[{"total": 7}])
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {
            "operation": "count",
            "time_start": "2026-04-01T00:00:00Z",
            "time_end": "2026-04-30T23:59:59Z",
        }
    )

    assert result == {
        "operation": "count",
        "entity": "events",
        "distinct": "events",
        "count": 7,
        "filters": {
            "time_start": "2026-04-01T00:00:00Z",
            "time_end": "2026-04-30T23:59:59Z",
        },
    }
    query, params = cursor.executed[0]
    assert "COUNT(DISTINCT e.id)" in query
    assert "e.start_date >= %s" in query
    assert "e.start_date <= %s" in query
    assert params == ("2026-04-01T00:00:00Z", "2026-04-30T23:59:59Z")


def test_count_distinct_contacts_joins_event_contacts(monkeypatch):
    cursor = _Cursor(results=[{"total": 12}])
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {
            "operation": "count",
            "distinct": "contacts",
            "time_start": "2026-04-01T00:00:00Z",
        }
    )

    assert result["count"] == 12
    assert result["distinct"] == "contacts"
    query, _ = cursor.executed[0]
    assert "COUNT(DISTINCT ec.contact_id)" in query
    assert "JOIN event_contacts ec" in query


def test_count_distinct_places_filters_null_place(monkeypatch):
    cursor = _Cursor(results=[{"total": 3}])
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph({"operation": "count", "distinct": "places"})

    assert result["count"] == 3
    query, _ = cursor.executed[0]
    assert "COUNT(DISTINCT e.place_id)" in query
    assert "e.place_id IS NOT NULL" in query


def test_count_lowercases_tags_and_types(monkeypatch):
    cursor = _Cursor(results=[{"total": 0}])
    _install_fake_db(monkeypatch, cursor)

    handle_query_graph(
        {
            "operation": "count",
            "tags": ["Work", "Apollo"],
            "types": ["Meeting"],
        }
    )
    query, params = cursor.executed[0]
    assert "unnest(COALESCE(e.tags" in query
    assert "unnest(COALESCE(e.types" in query
    assert ["work", "apollo"] in params
    assert ["meeting"] in params


def test_count_with_contact_and_place_filters(monkeypatch):
    cursor = _Cursor(results=[{"total": 1}])
    _install_fake_db(monkeypatch, cursor)

    handle_query_graph(
        {
            "operation": "count",
            "contact_ids": ["c-1"],
            "place_ids": ["p-1"],
        }
    )
    query, params = cursor.executed[0]
    assert "EXISTS (SELECT 1 FROM event_contacts ec" in query
    assert "e.place_id = ANY(%s)" in query
    assert ["c-1"] in params
    assert ["p-1"] in params


def test_count_returns_zero_when_cursor_empty(monkeypatch):
    cursor = _Cursor(results=[None])
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph({"operation": "count"})

    assert result["count"] == 0


def test_group_by_type_unnests_types(monkeypatch):
    cursor = _Cursor(
        results=[
            [
                {"bucket_key": "meeting", "bucket_count": 5},
                {"bucket_key": "communication", "bucket_count": 2},
            ]
        ]
    )
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph({"operation": "group_by", "group_by": "type"})

    assert result["operation"] == "group_by"
    assert result["group_by"] == "type"
    assert result["groups"] == [
        {"key": "meeting", "count": 5},
        {"key": "communication", "count": 2},
    ]
    query, params = cursor.executed[0]
    assert "unnest(COALESCE(e.types" in query
    assert "ORDER BY bucket_count DESC" in query
    assert params[-1] == 50  # default limit


def test_group_by_month_uses_to_char(monkeypatch):
    cursor = _Cursor(results=[[{"bucket_key": "2026-04", "bucket_count": 11}]])
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph({"operation": "group_by", "group_by": "month"})

    assert result["groups"] == [{"key": "2026-04", "count": 11}]
    query, _ = cursor.executed[0]
    assert "to_char(e.start_date, 'YYYY-MM')" in query


def test_group_by_place_resolves_names(monkeypatch):
    cursor = _Cursor(
        results=[
            [
                {"bucket_key": "place-1", "bucket_count": 4},
                {"bucket_key": "place-2", "bucket_count": 2},
            ],
            [
                {"place_id": "place-1", "name": "HQ"},
                {"place_id": "place-2", "name": "Coffee shop"},
            ],
        ]
    )
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph({"operation": "group_by", "group_by": "place"})

    assert result["groups"] == [
        {"key": "HQ", "place_id": "place-1", "count": 4},
        {"key": "Coffee shop", "place_id": "place-2", "count": 2},
    ]
    primary_query, _ = cursor.executed[0]
    assert "e.place_id IS NOT NULL" in primary_query
    lookup_query, lookup_params = cursor.executed[1]
    assert "FROM places" in lookup_query
    assert lookup_params == (["place-1", "place-2"],)


def test_group_by_contact_uses_resolve_contact_names(monkeypatch):
    cursor = _Cursor(
        results=[
            [
                {"bucket_key": "contact-1", "bucket_count": 6},
                {"bucket_key": "contact-2", "bucket_count": 3},
            ]
        ]
    )

    captured: dict = {}

    def fake_resolve(cur, ids):
        captured["ids"] = set(ids)
        return {"contact-1": "Alice", "contact-2": "Bob"}

    _install_fake_db(monkeypatch, cursor, resolve_contact_names=fake_resolve)

    result = handle_query_graph({"operation": "group_by", "group_by": "contact"})

    assert result["groups"] == [
        {"key": "Alice", "contact_id": "contact-1", "count": 6},
        {"key": "Bob", "contact_id": "contact-2", "count": 3},
    ]
    assert captured["ids"] == {"contact-1", "contact-2"}
    query, _ = cursor.executed[0]
    assert "JOIN event_contacts ec" in query


def test_group_by_clamps_limit_to_max_200(monkeypatch):
    cursor = _Cursor(results=[[]])
    _install_fake_db(monkeypatch, cursor)

    handle_query_graph({"operation": "group_by", "group_by": "type", "limit": 9999})

    _, params = cursor.executed[0]
    assert params[-1] == 200


def test_group_by_invalid_limit_falls_back_to_default(monkeypatch):
    cursor = _Cursor(results=[[]])
    _install_fake_db(monkeypatch, cursor)

    handle_query_graph({"operation": "group_by", "group_by": "type", "limit": "abc"})

    _, params = cursor.executed[0]
    assert params[-1] == 50


@pytest.mark.parametrize("group_by", ["week", "day", "tag"])
def test_group_by_supported_dimensions_execute(monkeypatch, group_by):
    cursor = _Cursor(results=[[]])
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph({"operation": "group_by", "group_by": group_by})

    assert "error" not in result
    assert result["group_by"] == group_by
    assert cursor.executed, f"{group_by} should issue a query"


# ---------------------------------------------------------------------------
# entity = "contacts"
# ---------------------------------------------------------------------------


def test_contacts_count_defaults_to_distinct_contacts(monkeypatch):
    cursor = _Cursor(results=[{"total": 42}])
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph({"entity": "contacts", "operation": "count"})

    assert result == {
        "operation": "count",
        "entity": "contacts",
        "distinct": "contacts",
        "count": 42,
        "filters": {},
    }
    query, params = cursor.executed[0]
    assert "COUNT(DISTINCT c.contact_id)" in query
    assert "FROM contacts c" in query
    assert params == ()


def test_contacts_count_distinct_places_joins_contact_places(monkeypatch):
    cursor = _Cursor(results=[{"total": 5}])
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {"entity": "contacts", "operation": "count", "distinct": "places"}
    )

    assert result["count"] == 5
    assert result["distinct"] == "places"
    query, _ = cursor.executed[0]
    assert "COUNT(DISTINCT cp.place_id)" in query
    assert "JOIN contact_places cp" in query


def test_contacts_count_filters_by_contact_and_place_ids(monkeypatch):
    cursor = _Cursor(results=[{"total": 3}])
    _install_fake_db(monkeypatch, cursor)

    handle_query_graph(
        {
            "entity": "contacts",
            "operation": "count",
            "contact_ids": ["c-1", "c-2"],
            "place_ids": ["p-7"],
            "tags": ["Friend"],
        }
    )
    query, params = cursor.executed[0]
    assert "c.contact_id = ANY(%s)" in query
    assert "EXISTS (SELECT 1 FROM contact_places cp" in query
    assert "unnest(COALESCE(c.tags" in query
    assert ["c-1", "c-2"] in params
    assert ["p-7"] in params
    assert ["friend"] in params


def test_contacts_rejects_distinct_events(monkeypatch):
    cursor = _Cursor()
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {"entity": "contacts", "operation": "count", "distinct": "events"}
    )

    assert "error" in result
    assert result["entity"] == "contacts"
    assert cursor.executed == []


def test_contacts_group_by_tag_unnests(monkeypatch):
    cursor = _Cursor(
        results=[
            [
                {"bucket_key": "friend", "bucket_count": 12},
                {"bucket_key": "work", "bucket_count": 8},
            ]
        ]
    )
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {"entity": "contacts", "operation": "group_by", "group_by": "tag"}
    )

    assert result["groups"] == [
        {"key": "friend", "count": 12},
        {"key": "work", "count": 8},
    ]
    query, _ = cursor.executed[0]
    assert "FROM contacts c, unnest(COALESCE(c.tags" in query
    assert "COUNT(DISTINCT c.contact_id)" in query


def test_contacts_group_by_place_resolves_names(monkeypatch):
    cursor = _Cursor(
        results=[
            [
                {"bucket_key": "place-1", "bucket_count": 4},
                {"bucket_key": "place-2", "bucket_count": 1},
            ],
            [
                {"place_id": "place-1", "name": "Aurora office"},
                {"place_id": "place-2", "name": "Cafe"},
            ],
        ]
    )
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {"entity": "contacts", "operation": "group_by", "group_by": "place"}
    )

    assert result["groups"] == [
        {"key": "Aurora office", "place_id": "place-1", "count": 4},
        {"key": "Cafe", "place_id": "place-2", "count": 1},
    ]
    primary_query, _ = cursor.executed[0]
    assert "JOIN contact_places cp" in primary_query
    assert "COUNT(DISTINCT c.contact_id)" in primary_query


def test_contacts_rejects_event_only_group_by(monkeypatch):
    cursor = _Cursor()
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {"entity": "contacts", "operation": "group_by", "group_by": "type"}
    )

    assert "error" in result
    assert cursor.executed == []


# ---------------------------------------------------------------------------
# entity = "places"
# ---------------------------------------------------------------------------


def test_places_count_defaults_to_distinct_places(monkeypatch):
    cursor = _Cursor(results=[{"total": 17}])
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph({"entity": "places", "operation": "count"})

    assert result["count"] == 17
    assert result["distinct"] == "places"
    query, _ = cursor.executed[0]
    assert "COUNT(DISTINCT p.place_id)" in query
    assert "FROM places p" in query


def test_places_count_distinct_events_joins_events(monkeypatch):
    cursor = _Cursor(results=[{"total": 9}])
    _install_fake_db(monkeypatch, cursor)

    handle_query_graph({"entity": "places", "operation": "count", "distinct": "events"})

    query, _ = cursor.executed[0]
    assert "COUNT(DISTINCT e.id)" in query
    assert "FROM places p JOIN events e ON e.place_id = p.place_id" in query


def test_places_count_distinct_contacts_joins_contact_places(monkeypatch):
    cursor = _Cursor(results=[{"total": 4}])
    _install_fake_db(monkeypatch, cursor)

    handle_query_graph(
        {"entity": "places", "operation": "count", "distinct": "contacts"}
    )

    query, _ = cursor.executed[0]
    assert "COUNT(DISTINCT cp.contact_id)" in query
    assert "JOIN contact_places cp" in query


def test_places_filters_by_contact_ids(monkeypatch):
    cursor = _Cursor(results=[{"total": 2}])
    _install_fake_db(monkeypatch, cursor)

    handle_query_graph(
        {
            "entity": "places",
            "operation": "count",
            "contact_ids": ["c-9"],
        }
    )
    query, params = cursor.executed[0]
    assert "EXISTS (SELECT 1 FROM contact_places cp" in query
    assert ["c-9"] in params


def test_places_group_by_city(monkeypatch):
    cursor = _Cursor(
        results=[
            [
                {"bucket_key": "Aurora", "bucket_count": 6},
                {"bucket_key": "Harborview", "bucket_count": 3},
            ]
        ]
    )
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {"entity": "places", "operation": "group_by", "group_by": "city"}
    )

    assert result["groups"] == [
        {"key": "Aurora", "count": 6},
        {"key": "Harborview", "count": 3},
    ]
    query, _ = cursor.executed[0]
    assert "p.city" in query
    assert "COUNT(DISTINCT p.place_id)" in query


def test_places_group_by_country(monkeypatch):
    cursor = _Cursor(results=[[{"bucket_key": "WT", "bucket_count": 11}]])
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {"entity": "places", "operation": "group_by", "group_by": "country"}
    )

    assert result["groups"] == [{"key": "WT", "count": 11}]
    query, _ = cursor.executed[0]
    assert "p.country" in query


# ---------------------------------------------------------------------------
# entity = "documents"
# ---------------------------------------------------------------------------


def test_documents_count_defaults_to_distinct_documents(monkeypatch):
    cursor = _Cursor(results=[{"total": 23}])
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph({"entity": "documents", "operation": "count"})

    assert result["count"] == 23
    assert result["distinct"] == "documents"
    query, _ = cursor.executed[0]
    assert "COUNT(DISTINCT d.document_id)" in query
    assert "FROM documents d" in query


def test_documents_count_filters_by_document_date_window(monkeypatch):
    cursor = _Cursor(results=[{"total": 4}])
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {
            "entity": "documents",
            "operation": "count",
            "time_start": "2026-01-01T00:00:00Z",
            "time_end": "2026-03-31T23:59:59Z",
            "tags": ["Apollo"],
        }
    )

    assert result["count"] == 4
    query, params = cursor.executed[0]
    assert "d.document_date >= %s" in query
    assert "d.document_date <= %s" in query
    assert "unnest(COALESCE(d.tags" in query
    assert "2026-01-01T00:00:00Z" in params
    assert "2026-03-31T23:59:59Z" in params
    assert ["apollo"] in params


def test_documents_count_can_scope_to_linked_contacts(monkeypatch):
    cursor = _Cursor(results=[{"total": 2}])
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {
            "entity": "documents",
            "operation": "count",
            "contact_ids": ["contact:daughter"],
        }
    )

    assert result["count"] == 2
    assert result["filters"]["contact_ids"] == ["contact:daughter"]
    query, params = cursor.executed[0]
    assert "document_contacts dc" in query
    assert ["contact:daughter"] in params


def test_documents_rejects_non_document_distinct(monkeypatch):
    cursor = _Cursor()
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {"entity": "documents", "operation": "count", "distinct": "events"}
    )

    assert "error" in result
    assert cursor.executed == []


def test_documents_can_count_distinct_contacts(monkeypatch):
    cursor = _Cursor(results=[{"total": 3}])
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {"entity": "documents", "operation": "count", "distinct": "contacts"}
    )

    assert result["count"] == 3
    assert result["distinct"] == "contacts"
    query, _ = cursor.executed[0]
    assert "JOIN document_contacts dc" in query


def test_documents_group_by_month_uses_document_date(monkeypatch):
    cursor = _Cursor(results=[[{"bucket_key": "2026-02", "bucket_count": 7}]])
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {"entity": "documents", "operation": "group_by", "group_by": "month"}
    )

    assert result["groups"] == [{"key": "2026-02", "count": 7}]
    query, _ = cursor.executed[0]
    assert "to_char(d.document_date, 'YYYY-MM')" in query
    assert "COUNT(DISTINCT d.document_id)" in query


def test_documents_group_by_file_mime(monkeypatch):
    cursor = _Cursor(
        results=[
            [
                {"bucket_key": "application/pdf", "bucket_count": 12},
                {"bucket_key": "text/markdown", "bucket_count": 5},
            ]
        ]
    )
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {"entity": "documents", "operation": "group_by", "group_by": "file_mime"}
    )

    assert result["groups"] == [
        {"key": "application/pdf", "count": 12},
        {"key": "text/markdown", "count": 5},
    ]
    query, _ = cursor.executed[0]
    assert "d.file_mime" in query


def test_documents_group_by_tag_unnests(monkeypatch):
    cursor = _Cursor(results=[[]])
    _install_fake_db(monkeypatch, cursor)

    handle_query_graph(
        {"entity": "documents", "operation": "group_by", "group_by": "tag"}
    )

    query, _ = cursor.executed[0]
    assert "FROM documents d, unnest(COALESCE(d.tags" in query


def test_documents_group_by_contact(monkeypatch):
    cursor = _Cursor(results=[[{"bucket_key": "contact:1", "bucket_count": 4}]])
    _install_fake_db(
        monkeypatch,
        cursor,
        resolve_contact_names=lambda _cur, _ids: {"contact:1": "Alice"},
    )

    result = handle_query_graph(
        {"entity": "documents", "operation": "group_by", "group_by": "contact"}
    )

    assert result["groups"] == [{"key": "Alice", "contact_id": "contact:1", "count": 4}]
    query, _ = cursor.executed[0]
    assert "JOIN document_contacts dc" in query


def test_documents_rejects_event_only_group_by(monkeypatch):
    cursor = _Cursor()
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {"entity": "documents", "operation": "group_by", "group_by": "place"}
    )

    assert "error" in result
    assert cursor.executed == []


# ---------------------------------------------------------------------------
# entity = "todos"
# ---------------------------------------------------------------------------


def test_todos_count_defaults_to_distinct_todos(monkeypatch):
    cursor = _Cursor(results=[{"total": 8}])
    _install_fake_db(monkeypatch, cursor)
    _install_fake_todos(monkeypatch)

    result = handle_query_graph({"entity": "todos", "operation": "count"})

    assert result == {
        "operation": "count",
        "entity": "todos",
        "distinct": "todos",
        "count": 8,
        "filters": {},
    }
    query, params = cursor.executed[0]
    assert "COUNT(DISTINCT t.todo_id)" in query
    assert "FROM todos t" in query
    assert params == ()


def test_todos_count_status_filter_includes_normalization_sql(monkeypatch):
    cursor = _Cursor(results=[{"total": 3}])
    _install_fake_db(monkeypatch, cursor)
    _install_fake_todos(monkeypatch)

    result = handle_query_graph(
        {"entity": "todos", "operation": "count", "status": "completed"}
    )

    assert result["count"] == 3
    assert result["filters"]["status"] == "completed"
    query, params = cursor.executed[0]
    assert "lower(coalesce(t.status" in query
    # status normalization adds 5 params; then the literal 'completed' for the equality test
    assert params[: len(_FAKE_STATUS_PARAMS)] == _FAKE_STATUS_PARAMS
    assert params[-1] == "completed"


def test_todos_count_default_time_field_is_due_date(monkeypatch):
    cursor = _Cursor(results=[{"total": 2}])
    _install_fake_db(monkeypatch, cursor)
    _install_fake_todos(monkeypatch)

    result = handle_query_graph(
        {
            "entity": "todos",
            "operation": "count",
            "time_start": "2026-04-01T00:00:00Z",
            "time_end": "2026-04-30T23:59:59Z",
        }
    )

    assert result["filters"]["time_field"] == "due"
    query, _ = cursor.executed[0]
    assert "t.due_date >= %s" in query
    assert "t.due_date <= %s" in query
    assert "t.updated_at" not in query


def test_todos_count_time_field_updated_uses_updated_at(monkeypatch):
    cursor = _Cursor(results=[{"total": 1}])
    _install_fake_db(monkeypatch, cursor)
    _install_fake_todos(monkeypatch)

    handle_query_graph(
        {
            "entity": "todos",
            "operation": "count",
            "time_field": "updated",
            "time_start": "2026-04-20T00:00:00Z",
        }
    )
    query, _ = cursor.executed[0]
    assert "t.updated_at >= %s" in query
    assert "t.due_date" not in query


def test_todos_count_invalid_time_field_falls_back_to_due(monkeypatch):
    cursor = _Cursor(results=[{"total": 0}])
    _install_fake_db(monkeypatch, cursor)
    _install_fake_todos(monkeypatch)

    result = handle_query_graph(
        {
            "entity": "todos",
            "operation": "count",
            "time_field": "bogus",
            "time_start": "2026-04-01T00:00:00Z",
        }
    )
    assert result["filters"]["time_field"] == "due"
    query, _ = cursor.executed[0]
    assert "t.due_date >= %s" in query


def test_todos_count_filters_by_event_ids_via_junction(monkeypatch):
    cursor = _Cursor(results=[{"total": 1}])
    _install_fake_db(monkeypatch, cursor)
    _install_fake_todos(monkeypatch)

    handle_query_graph(
        {"entity": "todos", "operation": "count", "event_ids": ["e-1", "e-2"]}
    )
    query, params = cursor.executed[0]
    assert "EXISTS (SELECT 1 FROM todo_events tev" in query
    assert ["e-1", "e-2"] in params


def test_todos_count_filters_by_contact_and_place_ids(monkeypatch):
    cursor = _Cursor(results=[{"total": 1}])
    _install_fake_db(monkeypatch, cursor)
    _install_fake_todos(monkeypatch)

    handle_query_graph(
        {
            "entity": "todos",
            "operation": "count",
            "contact_ids": ["c-1"],
            "place_ids": ["p-1"],
        }
    )
    query, params = cursor.executed[0]
    assert "EXISTS (SELECT 1 FROM todo_contacts tc" in query
    assert "EXISTS (SELECT 1 FROM todo_places tp" in query
    assert ["c-1"] in params
    assert ["p-1"] in params


def test_todos_rejects_non_todo_distinct(monkeypatch):
    cursor = _Cursor()
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {"entity": "todos", "operation": "count", "distinct": "events"}
    )

    assert "error" in result
    assert result["entity"] == "todos"
    assert cursor.executed == []


def test_todos_group_by_status_uses_status_expression(monkeypatch):
    cursor = _Cursor(
        results=[
            [
                {"bucket_key": "completed", "bucket_count": 9},
                {"bucket_key": "pending", "bucket_count": 4},
            ]
        ]
    )
    _install_fake_db(monkeypatch, cursor)
    _install_fake_todos(monkeypatch)

    result = handle_query_graph(
        {"entity": "todos", "operation": "group_by", "group_by": "status"}
    )

    assert result["groups"] == [
        {"key": "completed", "count": 9},
        {"key": "pending", "count": 4},
    ]
    query, params = cursor.executed[0]
    # status expression appears in SELECT (the key) and in GROUP BY
    assert query.count("CASE ") >= 2
    # the SELECT-clause status params come before the limit
    assert params[: len(_FAKE_STATUS_PARAMS)] == _FAKE_STATUS_PARAMS


def test_todos_group_by_month_default_uses_due_date(monkeypatch):
    cursor = _Cursor(results=[[{"bucket_key": "2026-04", "bucket_count": 6}]])
    _install_fake_db(monkeypatch, cursor)
    _install_fake_todos(monkeypatch)

    result = handle_query_graph(
        {"entity": "todos", "operation": "group_by", "group_by": "month"}
    )

    assert result["groups"] == [{"key": "2026-04", "count": 6}]
    assert result["filters"]["time_field"] == "due"
    query, _ = cursor.executed[0]
    assert "to_char(t.due_date, 'YYYY-MM')" in query


def test_todos_group_by_month_with_time_field_updated(monkeypatch):
    cursor = _Cursor(results=[[]])
    _install_fake_db(monkeypatch, cursor)
    _install_fake_todos(monkeypatch)

    result = handle_query_graph(
        {
            "entity": "todos",
            "operation": "group_by",
            "group_by": "month",
            "time_field": "updated",
        }
    )

    assert result["filters"]["time_field"] == "updated"
    query, _ = cursor.executed[0]
    assert "to_char(t.updated_at, 'YYYY-MM')" in query
    assert "t.due_date" not in query


def test_todos_group_by_contact_resolves_names(monkeypatch):
    cursor = _Cursor(
        results=[
            [
                {"bucket_key": "contact-1", "bucket_count": 5},
                {"bucket_key": "contact-2", "bucket_count": 2},
            ]
        ]
    )

    captured: dict = {}

    def fake_resolve(cur, ids):
        captured["ids"] = set(ids)
        return {"contact-1": "Alice", "contact-2": "Bob"}

    _install_fake_db(monkeypatch, cursor, resolve_contact_names=fake_resolve)
    _install_fake_todos(monkeypatch)

    result = handle_query_graph(
        {"entity": "todos", "operation": "group_by", "group_by": "contact"}
    )

    assert result["groups"] == [
        {"key": "Alice", "contact_id": "contact-1", "count": 5},
        {"key": "Bob", "contact_id": "contact-2", "count": 2},
    ]
    assert captured["ids"] == {"contact-1", "contact-2"}
    query, _ = cursor.executed[0]
    assert "JOIN todo_contacts tc" in query


def test_todos_rejects_event_only_group_by(monkeypatch):
    cursor = _Cursor()
    _install_fake_db(monkeypatch, cursor)

    result = handle_query_graph(
        {"entity": "todos", "operation": "group_by", "group_by": "type"}
    )

    assert "error" in result
    assert cursor.executed == []


def test_todos_finished_this_week_query_shape(monkeypatch):
    """End-to-end shape check matching the existing eval case."""
    cursor = _Cursor(results=[{"total": 4}])
    _install_fake_db(monkeypatch, cursor)
    _install_fake_todos(monkeypatch)

    result = handle_query_graph(
        {
            "entity": "todos",
            "operation": "count",
            "status": "completed",
            "time_field": "updated",
            "time_start": "2026-04-20T00:00:00Z",
        }
    )

    assert result["count"] == 4
    assert result["filters"] == {
        "time_start": "2026-04-20T00:00:00Z",
        "time_field": "updated",
        "status": "completed",
    }
    query, params = cursor.executed[0]
    assert "t.updated_at >= %s" in query
    assert "lower(coalesce(t.status" in query
    assert "2026-04-20T00:00:00Z" in params
    assert "completed" in params
