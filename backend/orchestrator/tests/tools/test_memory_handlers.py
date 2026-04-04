import sys
from types import SimpleNamespace

from tools.handlers.memory import (
    handle_get_events,
    handle_search_memories,
    handle_summarize_memories,
)


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


def test_get_events_by_time_span_applies_tags_and_types(monkeypatch):
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
            "tags": ["Work"],
            "types": ["meeting"],
            "limit": 10,
        }
    )

    assert result["count"] == 0
    query_text, query_params = executed[0]
    assert "unnest(COALESCE(e.tags" in query_text
    assert "unnest(COALESCE(e.types" in query_text
    assert ["work"] in query_params
    assert ["meeting"] in query_params


def test_summarize_memories_combines_events_and_documents(monkeypatch):
    def fake_search_memories(query, **kwargs):
        assert kwargs["tags"] == ["Work"]
        return {
            "results": [
                {
                    "id": "doc:1",
                    "kind": "document",
                    "title": "Project Apollo notes",
                    "tags": ["Work", "Apollo"],
                    "document_date": "2026-02-10T10:00:00+00:00",
                    "created_at": "2026-02-10T10:00:00+00:00",
                    "snippet": "Decision: ship phase one.",
                }
            ]
        }

    def fake_get_document(document_id):
        assert document_id == "doc:1"
        return {
            "document_id": document_id,
            "title": "Project Apollo notes",
            "tags": ["Work", "Apollo"],
            "document_date": "2026-02-10T10:00:00+00:00",
            "created_at": "2026-02-10T10:00:00+00:00",
            "content": "Decision: ship phase one. Follow up with design review.",
            "raw_metadata": {},
        }

    monkeypatch.setitem(sys.modules, "retrieval", SimpleNamespace(search_memories=fake_search_memories))
    monkeypatch.setitem(sys.modules, "documents", SimpleNamespace(get_document=fake_get_document))
    monkeypatch.setattr(
        "tools.handlers.memory.handle_get_events",
        lambda *args, **kwargs: {
            "events": [
                {
                    "id": "event:1",
                    "title": "Apollo weekly sync",
                    "start_date": "2026-02-10T09:00:00+00:00",
                    "summary": "Discussed launch scope and approval timing.",
                    "people": [{"display_name": "Alex"}],
                    "tags": ["Work", "Apollo"],
                    "types": ["meeting"],
                }
            ],
            "count": 1,
        },
    )
    monkeypatch.setattr(
        "tools.handlers.memory._synthesize_memory_summary",
        lambda **kwargs: "Overview\n- Apollo launch scope\n- Decision to ship phase one",
    )

    result = handle_summarize_memories(
        {
            "time_start": "2026-02-01T00:00:00Z",
            "time_end": "2026-02-28T23:59:59Z",
            "tags": ["Work"],
            "types": ["meeting"],
            "query_focus": "decisions",
        },
        question="What came out of my work discussions in February?",
    )

    assert "Apollo" in result["summary"]
    assert result["count"] == 2
    assert result["events"][0]["id"] == "event:1"
    assert result["documents"][0]["id"] == "doc:1"
    assert {item["kind"] for item in result["source_items"]} == {"event", "document"}
