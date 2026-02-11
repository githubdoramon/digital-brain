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
    monkeypatch.setattr(
        retrieval, "vector_search_contacts", lambda *_args, **_kwargs: [("contact-gio", 0.99)]
    )
    monkeypatch.setattr(
        retrieval, "vector_search_documents", lambda *_args, **_kwargs: {"doc-1": 0.99}
    )
    monkeypatch.setattr(
        retrieval, "bm25_search_documents", lambda *_args, **_kwargs: {"doc-1": 0.99}
    )
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
    monkeypatch.setattr(
        retrieval, "vector_search_contacts", lambda *_args, **_kwargs: [("contact-gio", 0.99)]
    )
    monkeypatch.setattr(
        retrieval, "vector_search_documents", lambda *_args, **_kwargs: {"doc-1": 0.99}
    )
    monkeypatch.setattr(
        retrieval, "bm25_search_documents", lambda *_args, **_kwargs: {"doc-1": 0.99}
    )
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


def test_relevance_with_time_filters_sorts_by_score_not_date(monkeypatch):
    monkeypatch.setattr(retrieval, "vector_search", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(retrieval, "bm25_search", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        retrieval,
        "structured_candidates",
        lambda *_args, **_kwargs: {"evt-a": 1.0, "evt-b": 1.0},
    )
    monkeypatch.setattr(
        retrieval,
        "structured_document_candidates",
        lambda *_args, **_kwargs: {"doc-a": 1.0, "doc-b": 1.0},
    )
    monkeypatch.setattr(
        retrieval,
        "vector_search_documents",
        lambda *_args, **_kwargs: {"doc-a": 0.9, "doc-b": 0.8},
    )
    monkeypatch.setattr(
        retrieval,
        "bm25_search_documents",
        lambda *_args, **_kwargs: {"doc-a": 0.9, "doc-b": 0.8},
    )
    monkeypatch.setattr(
        retrieval,
        "fetch_events",
        lambda *_args, **_kwargs: [
            _event_row("evt-a", datetime(2026, 1, 5, 10, 0, 0)),
            _event_row("evt-b", datetime(2026, 1, 10, 10, 0, 0)),
        ],
    )
    monkeypatch.setattr(
        retrieval,
        "fetch_document_summaries",
        lambda *_args, **_kwargs: {
            "doc-a": {
                "document_id": "doc-a",
                "title": "Doc A",
                "description": "Doc A summary",
                "tags": [],
                "document_date": datetime(2026, 1, 7, 10, 0, 0),
                "created_at": datetime(2026, 1, 7, 10, 0, 0),
                "updated_at": datetime(2026, 1, 7, 10, 0, 0),
                "download_url": "/documents/doc-a/download",
                "file_name": "doc-a.txt",
                "file_mime": "text/plain",
                "file_size": 123,
                "snippet": "Doc A summary",
            },
            "doc-b": {
                "document_id": "doc-b",
                "title": "Doc B",
                "description": "Doc B summary",
                "tags": [],
                "document_date": datetime(2026, 1, 12, 10, 0, 0),
                "created_at": datetime(2026, 1, 12, 10, 0, 0),
                "updated_at": datetime(2026, 1, 12, 10, 0, 0),
                "download_url": "/documents/doc-b/download",
                "file_name": "doc-b.txt",
                "file_mime": "text/plain",
                "file_size": 123,
                "snippet": "Doc B summary",
            },
        },
    )

    result = retrieval.search_memories(
        query="what happened around early january",
        time_start="2026-01-01T00:00:00",
        time_end="2026-01-31T23:59:59",
        limit=4,
    )

    ids = [item["id"] for item in result["results"]]
    kinds = [item["kind"] for item in result["results"]]
    assert ids == ["doc-a", "doc-b", "evt-a", "evt-b"]
    assert kinds == ["document", "document", "event", "event"]


def test_time_filter_applies_to_documents(monkeypatch):
    monkeypatch.setattr(retrieval, "vector_search", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(retrieval, "bm25_search", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(retrieval, "structured_candidates", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        retrieval,
        "vector_search_documents",
        lambda *_args, **_kwargs: {"doc-in": 0.9, "doc-out": 0.95},
    )
    monkeypatch.setattr(
        retrieval,
        "bm25_search_documents",
        lambda *_args, **_kwargs: {"doc-in": 0.9, "doc-out": 0.95},
    )
    monkeypatch.setattr(
        retrieval,
        "structured_document_candidates",
        lambda *_args, **_kwargs: {"doc-in": 1.0},
    )
    monkeypatch.setattr(retrieval, "fetch_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        retrieval,
        "fetch_document_summaries",
        lambda *_args, **_kwargs: {
            "doc-in": {
                "document_id": "doc-in",
                "title": "Doc In",
                "description": "Inside range",
                "tags": [],
                "document_date": datetime(2026, 1, 7, 10, 0, 0),
                "created_at": datetime(2026, 1, 7, 10, 0, 0),
                "updated_at": datetime(2026, 1, 7, 10, 0, 0),
                "download_url": "/documents/doc-in/download",
                "file_name": "doc-in.txt",
                "file_mime": "text/plain",
                "file_size": 123,
                "snippet": "Inside range",
            },
            "doc-out": {
                "document_id": "doc-out",
                "title": "Doc Out",
                "description": "Outside range",
                "tags": [],
                "document_date": datetime(2025, 1, 7, 10, 0, 0),
                "created_at": datetime(2025, 1, 7, 10, 0, 0),
                "updated_at": datetime(2025, 1, 7, 10, 0, 0),
                "download_url": "/documents/doc-out/download",
                "file_name": "doc-out.txt",
                "file_mime": "text/plain",
                "file_size": 123,
                "snippet": "Outside range",
            },
        },
    )

    result = retrieval.search_memories(
        query="vitamin b12 record",
        time_start="2026-01-01T00:00:00",
        time_end="2026-01-31T23:59:59",
        limit=5,
    )

    ids = [item["id"] for item in result["results"]]
    assert ids == ["doc-in"]


def test_structured_filters_do_not_force_event_first_ordering(monkeypatch):
    monkeypatch.setattr(retrieval, "vector_search", lambda *_args, **_kwargs: {"evt-a": 0.1})
    monkeypatch.setattr(retrieval, "bm25_search", lambda *_args, **_kwargs: {"evt-a": 0.1})
    monkeypatch.setattr(
        retrieval,
        "structured_candidates",
        lambda *_args, **_kwargs: {"evt-a": 1.0},
    )
    monkeypatch.setattr(
        retrieval,
        "vector_search_documents",
        lambda *_args, **_kwargs: {"doc-a": 0.99},
    )
    monkeypatch.setattr(
        retrieval,
        "bm25_search_documents",
        lambda *_args, **_kwargs: {"doc-a": 0.99},
    )
    monkeypatch.setattr(
        retrieval,
        "fetch_events",
        lambda *_args, **_kwargs: [_event_row("evt-a", datetime(2026, 1, 1, 10, 0, 0))],
    )
    monkeypatch.setattr(
        retrieval,
        "fetch_document_summaries",
        lambda *_args, **_kwargs: {
            "doc-a": {
                "document_id": "doc-a",
                "title": "Doc A",
                "description": "Doc A summary",
                "tags": [],
                "document_date": datetime(2026, 1, 2, 10, 0, 0),
                "created_at": datetime(2026, 1, 2, 10, 0, 0),
                "updated_at": datetime(2026, 1, 2, 10, 0, 0),
                "download_url": "/documents/doc-a/download",
                "file_name": "doc-a.txt",
                "file_mime": "text/plain",
                "file_size": 123,
                "snippet": "Doc A summary",
            }
        },
    )

    result = retrieval.search_memories(
        query="gio notes",
        people=["contact-gio"],
        sort_order="relevance",
        limit=1,
    )

    assert result["results"][0]["id"] == "doc-a"
    assert result["results"][0]["kind"] == "document"


def test_tag_filter_constrains_event_and_document_candidates(monkeypatch):
    monkeypatch.setattr(
        retrieval, "vector_search", lambda *_args, **_kwargs: {"evt-a": 0.9, "evt-b": 0.95}
    )
    monkeypatch.setattr(
        retrieval, "bm25_search", lambda *_args, **_kwargs: {"evt-a": 0.8, "evt-b": 0.85}
    )
    monkeypatch.setattr(
        retrieval,
        "vector_search_documents",
        lambda *_args, **_kwargs: {"doc-a": 0.95, "doc-b": 0.99},
    )
    monkeypatch.setattr(
        retrieval,
        "bm25_search_documents",
        lambda *_args, **_kwargs: {"doc-a": 0.85, "doc-b": 0.9},
    )

    def _structured_events(_timespan, _people, _places, tags, _limit):
        assert tags == ["health"]
        return {"evt-a": 1.0}

    def _structured_docs(_timespan, tags, _limit):
        assert tags == ["health"]
        return {"doc-a": 1.0}

    monkeypatch.setattr(retrieval, "structured_candidates", _structured_events)
    monkeypatch.setattr(retrieval, "structured_document_candidates", _structured_docs)
    monkeypatch.setattr(
        retrieval,
        "fetch_events",
        lambda *_args, **_kwargs: [_event_row("evt-a", datetime(2026, 1, 1, 10, 0, 0))],
    )
    monkeypatch.setattr(
        retrieval,
        "fetch_document_summaries",
        lambda *_args, **_kwargs: {
            "doc-a": {
                "document_id": "doc-a",
                "title": "Doc A",
                "description": "Doc A summary",
                "tags": ["health"],
                "document_date": datetime(2026, 1, 2, 10, 0, 0),
                "created_at": datetime(2026, 1, 2, 10, 0, 0),
                "updated_at": datetime(2026, 1, 2, 10, 0, 0),
                "download_url": "/documents/doc-a/download",
                "file_name": "doc-a.txt",
                "file_mime": "text/plain",
                "file_size": 123,
                "snippet": "Doc A summary",
            }
        },
    )

    result = retrieval.search_memories(query="health summary", tags=["health"], limit=10)

    ids = [item["id"] for item in result["results"]]
    assert set(ids) == {"evt-a", "doc-a"}


def test_tag_filter_without_query_uses_structured_candidates(monkeypatch):
    monkeypatch.setattr(retrieval, "vector_search", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(retrieval, "bm25_search", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(retrieval, "vector_search_documents", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(retrieval, "bm25_search_documents", lambda *_args, **_kwargs: {})

    monkeypatch.setattr(
        retrieval,
        "structured_candidates",
        lambda *_args, **_kwargs: {"evt-tag": 1.0},
    )
    monkeypatch.setattr(
        retrieval,
        "structured_document_candidates",
        lambda *_args, **_kwargs: {"doc-tag": 1.0},
    )
    monkeypatch.setattr(
        retrieval,
        "fetch_events",
        lambda *_args, **_kwargs: [_event_row("evt-tag", datetime(2026, 1, 1, 10, 0, 0))],
    )
    monkeypatch.setattr(
        retrieval,
        "fetch_document_summaries",
        lambda *_args, **_kwargs: {
            "doc-tag": {
                "document_id": "doc-tag",
                "title": "Doc Tag",
                "description": "Tagged",
                "tags": ["work"],
                "document_date": datetime(2026, 1, 2, 10, 0, 0),
                "created_at": datetime(2026, 1, 2, 10, 0, 0),
                "updated_at": datetime(2026, 1, 2, 10, 0, 0),
                "download_url": "/documents/doc-tag/download",
                "file_name": "doc-tag.txt",
                "file_mime": "text/plain",
                "file_size": 123,
                "snippet": "Tagged",
            }
        },
    )

    result = retrieval.search_memories(query="", tags=["work"], limit=10)

    ids = [item["id"] for item in result["results"]]
    assert set(ids) == {"evt-tag", "doc-tag"}
