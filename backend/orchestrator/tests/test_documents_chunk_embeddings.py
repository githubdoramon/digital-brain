from __future__ import annotations

import pytest

import documents


def test_chunk_text_for_embedding_respects_overlap_and_limit():
    text = "abcdefghijklmnopqrstuvwxyz" * 30
    chunks = documents._chunk_text_for_embedding(
        text,
        chunk_chars=240,
        overlap_chars=60,
        max_chunks=4,
    )

    assert len(chunks) == 4
    assert chunks[0] == text[0:240]
    assert chunks[1] == text[180:420]
    assert chunks[2] == text[360:600]
    assert chunks[3] == text[540:780]


def test_generate_document_embeddings_averages_chunk_vectors(monkeypatch):
    monkeypatch.setattr(
        documents,
        "_chunk_text_for_embedding",
        lambda _text: ["aaaa", "bbbbbb"],
    )
    monkeypatch.setattr(documents, "_build_chunk_metadata_prefix", lambda _doc: "")
    monkeypatch.setattr(
        documents,
        "embed_text",
        lambda text: [float(len(text)), 1.0],
    )

    embedding, chunk_embeddings = documents._generate_document_embeddings(
        {
            "document_id": "doc-1",
            "content": "ignored by mocked chunker",
            "title": "My Doc",
            "description": "Summary",
            "tags": ["alpha"],
            "file_name": "doc.txt",
        }
    )

    assert embedding == [5.0, 1.0]
    assert len(chunk_embeddings) == 2
    assert [chunk.chunk_text for chunk in chunk_embeddings] == ["aaaa", "bbbbbb"]


def test_generate_document_embeddings_prefixes_metadata(monkeypatch):
    monkeypatch.setattr(documents, "_chunk_text_for_embedding", lambda _text: ["chunk body"])
    monkeypatch.setattr(documents, "embed_text", lambda _text: [0.5, 0.5])

    _embedding, chunk_embeddings = documents._generate_document_embeddings(
        {
            "document_id": "doc-1",
            "content": "chunk body",
            "title": "Budget Q1",
            "description": "Quarterly budget notes",
            "tags": ["finance", "planning"],
            "file_name": "budget.md",
        }
    )

    assert len(chunk_embeddings) == 1
    payload = chunk_embeddings[0].chunk_text
    assert payload.startswith("title: Budget Q1")
    assert "tags: finance, planning" in payload
    assert "\n\nchunk body" in payload


def test_vector_search_documents_uses_chunk_scores_with_legacy_fallback(monkeypatch):
    monkeypatch.setattr(documents, "embed_text", lambda _text: [0.1, 0.2])

    class FakeCursor:
        def __init__(self) -> None:
            self._rows: list[dict] = []

        def execute(self, query: str, params=None) -> None:
            if "WITH ranked_chunks AS" in query:
                self._rows = [
                    {"document_id": "doc-1", "best_score": 0.9, "mean_score": 0.5, "doc_score": 0.6},
                    {"document_id": "doc-2", "best_score": 0.8, "mean_score": 0.8, "doc_score": 0.2},
                ]
                return
            if "FROM documents" in query:
                self._rows = [{"document_id": "doc-legacy", "score": 0.4}]
                return
            self._rows = []

        def fetchall(self) -> list[dict]:
            return self._rows

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    class FakeConn:
        def __init__(self) -> None:
            self.cursor_obj = FakeCursor()

        def cursor(self):
            return self.cursor_obj

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(documents, "get_conn", lambda: FakeConn())

    scores = documents._vector_search_documents("meeting notes", 3)

    assert set(scores.keys()) == {"doc-1", "doc-2", "doc-legacy"}
    assert scores["doc-1"] == pytest.approx(0.77)
    assert scores["doc-2"] == pytest.approx(0.71)
    assert scores["doc-legacy"] == pytest.approx(0.4)
