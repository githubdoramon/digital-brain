from __future__ import annotations

from contextlib import contextmanager

import events


def test_delete_event_rejects_blank_id(monkeypatch):
    monkeypatch.setattr(
        "events.get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("get_conn should not be called")),
    )

    assert events.delete_event("   ") is False


class _FakeCursor:
    def __init__(self, *, event_deleted: bool):
        self.event_deleted = event_deleted
        self.rowcount = 0
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query: str, params: tuple[object, ...]):
        compact_query = " ".join(query.split())
        self.executed.append((compact_query, params))
        if "DELETE FROM events" in compact_query:
            self.rowcount = 1 if self.event_deleted else 0


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.commit_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commit_calls += 1


def test_delete_event_removes_links_and_event(monkeypatch):
    fake_cursor = _FakeCursor(event_deleted=True)
    fake_connection = _FakeConnection(fake_cursor)

    @contextmanager
    def fake_get_conn():
        yield fake_connection

    monkeypatch.setattr("events.get_conn", fake_get_conn)

    deleted = events.delete_event("event:alpha")

    assert deleted is True
    assert fake_connection.commit_calls == 1
    assert [query for query, _params in fake_cursor.executed] == [
        "DELETE FROM event_contacts WHERE event_id = %s",
        "DELETE FROM todo_events WHERE event_id = %s",
        "DELETE FROM events WHERE id = %s",
    ]


def test_delete_event_returns_false_when_missing(monkeypatch):
    fake_cursor = _FakeCursor(event_deleted=False)
    fake_connection = _FakeConnection(fake_cursor)

    @contextmanager
    def fake_get_conn():
        yield fake_connection

    monkeypatch.setattr("events.get_conn", fake_get_conn)

    deleted = events.delete_event("event:missing")

    assert deleted is False
    assert fake_connection.commit_calls == 1
