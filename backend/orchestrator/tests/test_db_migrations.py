from __future__ import annotations

from contextlib import contextmanager

import pytest

import db_migrations


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple | None]] = []
        self._applied_rows: list[dict[str, str]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query: str, params=None):
        sql = str(query).strip()
        self.executed.append((sql, params))
        if sql.startswith("INSERT INTO schema_migrations") and params:
            self._applied_rows.append({"version": params[0], "checksum": params[2]})

    def fetchall(self):
        return list(self._applied_rows)


class _FakeConn:
    def __init__(self) -> None:
        self.cursor_obj = _FakeCursor()
        self.commit_count = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commit_count += 1


def test_load_migration_files_orders_and_filters(tmp_path):
    (tmp_path / "0002_second.sql").write_text("SELECT 2;", encoding="utf-8")
    (tmp_path / "invalid-name.sql").write_text("SELECT 0;", encoding="utf-8")
    (tmp_path / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")

    files = db_migrations._load_migration_files(tmp_path)
    assert [item.filename for item in files] == ["0001_first.sql", "0002_second.sql"]


def test_load_migration_files_rejects_duplicate_versions(tmp_path):
    (tmp_path / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0001_second.sql").write_text("SELECT 2;", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Duplicate migration version"):
        db_migrations._load_migration_files(tmp_path)


def test_run_pending_migrations_applies_new_files(monkeypatch, tmp_path):
    migration_file = tmp_path / "0001_add_column.sql"
    migration_file.write_text("ALTER TABLE places ADD COLUMN test_col TEXT;", encoding="utf-8")

    fake_conn = _FakeConn()

    @contextmanager
    def _fake_get_conn():
        yield fake_conn

    monkeypatch.setattr(db_migrations, "_MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(db_migrations, "get_conn", _fake_get_conn)
    monkeypatch.setenv("DB_AUTO_MIGRATE", "true")

    applied = db_migrations.run_pending_migrations()

    assert applied == ["0001_add_column.sql"]
    assert any(
        sql.startswith("ALTER TABLE places ADD COLUMN test_col TEXT")
        for sql, _params in fake_conn.cursor_obj.executed
    )
