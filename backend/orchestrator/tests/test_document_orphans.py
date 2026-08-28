from __future__ import annotations

from pathlib import Path

from scripts import find_document_orphans


def test_find_document_orphans_reports_both_directions(monkeypatch, tmp_path: Path):
    referenced = tmp_path / "referenced.pdf"
    orphan = tmp_path / "orphan.pdf"
    referenced.write_bytes(b"ok")
    orphan.write_bytes(b"orphan")

    class Cursor:
        def execute(self, _query):
            return None

        def fetchall(self):
            return [
                {"document_id": "doc:present", "file_path": str(referenced)},
                {"document_id": "doc:missing", "file_path": str(tmp_path / "gone.pdf")},
            ]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(find_document_orphans, "get_conn", lambda: Connection())

    result = find_document_orphans.find_document_orphans(tmp_path)

    assert result["orphan_files"] == [str(orphan.resolve())]
    assert result["missing_db_files"] == [
        {"document_id": "doc:missing", "file_path": str(tmp_path / "gone.pdf")}
    ]
