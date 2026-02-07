"""Temporal ordering tests for retrieval.search_memories."""

import os
from datetime import datetime

os.environ.setdefault("DOCUMENT_STORAGE_DIR", "/tmp/digital-brain-test-documents")
import retrieval


def _event_row(event_id: str, start_date: datetime) -> dict:
    return {
        "id": event_id,
        "start_date": start_date,
        "end_date": None,
        "title": f"Event {event_id}",
        "summary": f"Summary {event_id}",
        "place_id": None,
        "place_name": None,
        "city": None,
        "country": None,
        "people": ["contact-gio"],
        "tags": [],
        "types": ["meeting"],
    }


def test_temporal_structured_newest_is_deterministic_and_event_only(monkeypatch):
    monkeypatch.setattr(retrieval, "vector_search", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(retrieval, "bm25_search", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(retrieval, "vector_search_contacts", lambda *_args, **_kwargs: [("contact-gio", 0.99)])
    monkeypatch.setattr(retrieval, "vector_search_documents", lambda *_args, **_kwargs: {"doc-1": 0.99})
    monkeypatch.setattr(retrieval, "bm25_search_documents", lambda *_args, **_kwargs: {"doc-1": 0.99})
    monkeypatch.setattr(
        retrieval,
        "structured_candidates",
        lambda *_args, **_kwargs: {"evt-a": 1.0, "evt-b": 1.0, "evt-c": 1.0},
    )
    monkeypatch.setattr(
        retrieval,
        "fetch_events",
        lambda *_args, **_kwargs: [
            _event_row("evt-b", datetime(2026, 1, 1, 10, 0, 0)),
            _event_row("evt-a", datetime(2026, 1, 1, 10, 0, 0)),
            _event_row("evt-c", datetime(2026, 2, 1, 10, 0, 0)),
        ],
    )
    monkeypatch.setattr(retrieval, "fetch_document_summaries", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(retrieval, "fetch_contact_summaries", lambda *_args, **_kwargs: {})

    result = retrieval.search_memories(
        query="events",
        people=["contact-gio"],
        sort_order="newest",
        limit=3,
    )

    ids = [item["id"] for item in result["results"]]
    kinds = [item["kind"] for item in result["results"]]
    assert ids == ["evt-c", "evt-b", "evt-a"]
    assert kinds == ["event", "event", "event"]


def test_temporal_structured_oldest_is_deterministic_and_event_only(monkeypatch):
    monkeypatch.setattr(retrieval, "vector_search", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(retrieval, "bm25_search", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(retrieval, "vector_search_contacts", lambda *_args, **_kwargs: [("contact-gio", 0.99)])
    monkeypatch.setattr(retrieval, "vector_search_documents", lambda *_args, **_kwargs: {"doc-1": 0.99})
    monkeypatch.setattr(retrieval, "bm25_search_documents", lambda *_args, **_kwargs: {"doc-1": 0.99})
    monkeypatch.setattr(
        retrieval,
        "structured_candidates",
        lambda *_args, **_kwargs: {"evt-a": 1.0, "evt-b": 1.0, "evt-c": 1.0},
    )
    monkeypatch.setattr(
        retrieval,
        "fetch_events",
        lambda *_args, **_kwargs: [
            _event_row("evt-b", datetime(2026, 1, 1, 10, 0, 0)),
            _event_row("evt-a", datetime(2026, 1, 1, 10, 0, 0)),
            _event_row("evt-c", datetime(2026, 2, 1, 10, 0, 0)),
        ],
    )
    monkeypatch.setattr(retrieval, "fetch_document_summaries", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(retrieval, "fetch_contact_summaries", lambda *_args, **_kwargs: {})

    result = retrieval.search_memories(
        query="events",
        people=["contact-gio"],
        sort_order="oldest",
        limit=3,
    )

    ids = [item["id"] for item in result["results"]]
    kinds = [item["kind"] for item in result["results"]]
    assert ids == ["evt-a", "evt-b", "evt-c"]
    assert kinds == ["event", "event", "event"]


def test_search_memories_does_not_call_contact_vector_search(monkeypatch):
    monkeypatch.setattr(retrieval, "vector_search", lambda *_args, **_kwargs: {"evt-a": 0.9})
    monkeypatch.setattr(retrieval, "bm25_search", lambda *_args, **_kwargs: {"evt-a": 0.8})
    monkeypatch.setattr(retrieval, "vector_search_documents", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(retrieval, "bm25_search_documents", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(retrieval, "structured_candidates", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        retrieval,
        "fetch_events",
        lambda *_args, **_kwargs: [_event_row("evt-a", datetime(2026, 1, 1, 10, 0, 0))],
    )
    monkeypatch.setattr(retrieval, "fetch_document_summaries", lambda *_args, **_kwargs: {})

    called = {"count": 0}

    def fail_if_called(*_args, **_kwargs):
        called["count"] += 1
        raise AssertionError("vector_search_contacts should not be called by search_memories")

    monkeypatch.setattr(retrieval, "vector_search_contacts", fail_if_called)

    result = retrieval.search_memories(query="gio sync", limit=1)

    assert called["count"] == 0
    assert result["results"][0]["kind"] == "event"
