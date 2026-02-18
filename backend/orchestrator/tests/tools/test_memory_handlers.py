import sys
from types import SimpleNamespace

from tools.handlers.memory import handle_get_events, handle_search_memories


def test_get_events_by_ids_uses_events_service(monkeypatch):
    fake_events_service = SimpleNamespace(
        get_events=lambda event_ids: [
            {
                "id": event_ids[0],
                "title": "Sample event",
                "start_date": "2026-02-10T10:00:00+00:00",
                "end_date": "2026-02-10T10:30:00+00:00",
                "people": ["contact:alice"],
            }
        ]
    )
    monkeypatch.setitem(sys.modules, "events", fake_events_service)

    result = handle_get_events({"action": "by_ids", "event_ids": ["event:1"]})

    assert result["count"] == 1
    assert result["events"][0]["id"] == "event:1"


def test_get_events_by_time_span_requires_bounds():
    result = handle_get_events({"action": "by_time_span", "time_start": "2026-02-01T00:00:00Z"})

    assert "error" in result
    assert "time_start and time_end" in result["error"]


def test_search_memories_all_query_uses_unbounded_limit(monkeypatch):
    calls: dict[str, object] = {}

    def fake_search_memories(query, **kwargs):
        calls["query"] = query
        calls["limit"] = kwargs.get("limit")
        return {"results": []}

    monkeypatch.setitem(
        sys.modules, "retrieval", SimpleNamespace(search_memories=fake_search_memories)
    )

    handle_search_memories(
        {
            "query": "gmail",
            "limit": 5,
        },
        question="list all memories about gmail",
    )

    assert calls["query"] == "gmail"
    assert calls["limit"] is None


def test_get_events_all_query_uses_unbounded_sql(monkeypatch):
    executed: list[tuple[str, tuple[object, ...]]] = []

    class _Cursor:
        def execute(self, query, params):
            executed.append((query, params))

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Conn:
        def cursor(self):
            return _Cursor()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    fake_db = SimpleNamespace(
        get_conn=lambda: _Conn(),
        fetch_event_people=lambda cur, event_ids: {},
        resolve_contact_names=lambda cur, contact_ids: {},
        enrich_people=lambda people, names: people,
    )
    monkeypatch.setitem(sys.modules, "events", SimpleNamespace(get_events=lambda _ids: []))
    monkeypatch.setitem(sys.modules, "db", fake_db)

    result = handle_get_events(
        {
            "action": "by_time_span",
            "time_start": "2026-02-01T00:00:00Z",
            "time_end": "2026-02-28T23:59:59Z",
            "limit": 10,
        },
        question="list all events from february",
    )

    assert result["count"] == 0
    assert executed
    query_text, query_params = executed[0]
    assert "LIMIT %s" not in query_text
    assert len(query_params) == 2
