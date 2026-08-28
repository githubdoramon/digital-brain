from __future__ import annotations

import pytest

import documents


class _Cursor:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self._rows: list[dict] = []

    def execute(self, query: str, _params=None) -> None:
        self.queries.append(query)
        if "FROM documents" in query:
            self._rows = [
                {
                    "document_id": "doc:1",
                    "title": "Example",
                    "tags": [],
                    "file_name": "example.txt",
                    "enhancement_status": "pending",
                }
            ]
        else:
            self._rows = []

    def fetchall(self) -> list[dict]:
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_list_documents_supports_document_date_sort_and_missing_filter(monkeypatch):
    cursor = _Cursor()
    monkeypatch.setattr(documents, "get_conn", lambda: _Connection(cursor))

    result = documents.list_documents(
        sort_by="document_date",
        sort_direction="asc",
        missing_enhancement=True,
    )

    assert result[0]["document_id"] == "doc:1"
    query = cursor.queries[0]
    assert "enhancement_status <> %s" in query
    assert "CASE WHEN document_date IS NULL THEN 1 ELSE 0 END" in query
    assert "document_date ASC" in query


def test_list_documents_rejects_unknown_sort_values(monkeypatch):
    monkeypatch.setattr(documents, "get_conn", lambda: _Connection(_Cursor()))

    with pytest.raises(ValueError, match="sort_by"):
        documents.list_documents(sort_by="title")
