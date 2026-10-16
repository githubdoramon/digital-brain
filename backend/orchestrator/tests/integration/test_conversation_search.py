"""Real pgvector checks; opt in with CONVERSATION_TEST_DSN pointing at a disposable DB."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

import conversation_search as search
import conversations

pytestmark = pytest.mark.skipif(
    not os.getenv("CONVERSATION_TEST_DSN"), reason="Disposable PostgreSQL not configured"
)


@pytest.fixture
def database(monkeypatch):
    monkeypatch.setattr(search, "_QUERY_CACHE", search.OrderedDict())
    monkeypatch.setattr(search, "_QUERY_FAILURE_UNTIL", 0.0)
    dsn = os.environ["CONVERSATION_TEST_DSN"]
    schema = "conversation_check_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    @contextmanager
    def connect():
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            yield conn

    root = Path(__file__).resolve().parents[4]
    bootstrap = (root / "backend/db/init.sql").read_text()
    tables = bootstrap[
        bootstrap.index("CREATE TABLE IF NOT EXISTS conversation_threads (") : bootstrap.index(
            "-- Durable per-message indexing work."
        )
    ]
    with connect() as conn:
        conn.execute(tables)
        conn.execute(
            "INSERT INTO conversation_threads (id, user_email, title) VALUES ('mine', 'user@example.com', 'Garden plans'), ('other', 'other@example.com', 'Other plans'), ('current', 'user@example.com', 'Current chat')"
        )
        conn.execute(
            "INSERT INTO conversation_messages (thread_id, role, content) VALUES ('mine', 'user', 'Jardín: plant watering schedule'), ('other', 'user', 'Private plant watering schedule'), ('current', 'user', 'Current plant watering schedule')"
        )
        # Existing messages exercise the initial migration backfill.
        conn.execute(
            (
                root / "backend/orchestrator/db_migrations/0072_conversation_semantic_search.sql"
            ).read_text()
        )
    monkeypatch.setattr(search, "get_conn", connect)
    monkeypatch.setattr(conversations, "get_conn", connect)
    monkeypatch.setattr(search.embeddings, "embed_text", lambda text, **kwargs: [1.0] + [0.0] * 767)
    try:
        yield connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def drain():
    search.prepare_backfill()
    while search.process_due_once():
        pass


def test_backfill_semantic_keyword_owner_scope_and_history(database):
    drain()
    hits = search.search_messages(
        "user@example.com", "moisture timing", exclude_thread_id="current"
    )
    assert {row["thread_id"] for row in hits} == {"mine"}
    assert hits[0]["role"] == "user"
    exact = search.search_messages("user@example.com", "jardin", exclude_thread_id="current")
    assert "keyword" in exact[0]["match_sources"]
    history = conversations.search_threads("user@example.com", "moisture timing")
    assert {row["id"] for row in history} == {"mine", "current"}
    assert all(row["matched_message_id"] for row in history)


def test_message_dates_context_and_deletion_cascade(database):
    drain()
    with database() as conn:
        conn.execute(
            "UPDATE conversation_messages SET created_at = '2020-01-01' WHERE thread_id = 'mine'"
        )
    assert not search.search_messages(
        "user@example.com", "watering", exclude_thread_id="current", time_start="2024-01-01"
    )
    mine = search.search_messages("user@example.com", "watering", exclude_thread_id="current")[0]
    assert search.get_context("other@example.com", "mine", mine["message_id"]) is None
    assert (
        search.get_context("user@example.com", "mine", mine["message_id"])["messages"][0]["role"]
        == "user"
    )
    assert conversations.delete_thread("mine", "user@example.com")
    with database() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM conversation_chunks WHERE message_id = %s",
                (mine["message_id"],),
            ).fetchone()["n"]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM conversation_message_index WHERE message_id = %s",
                (mine["message_id"],),
            ).fetchone()["n"]
            == 0
        )
    assert search.get_context("user@example.com", "mine", mine["message_id"]) is None


def test_update_during_inference_cannot_publish_stale_chunks(database, monkeypatch):
    def embed(text, **kwargs):
        with database() as conn:
            conn.execute(
                "UPDATE conversation_messages SET content = 'Corrected watering interval' WHERE thread_id = 'mine'"
            )
        return [1.0] + [0.0] * 767

    monkeypatch.setattr(search.embeddings, "embed_text", embed)
    assert search.process_due_once()
    with database() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM conversation_chunks").fetchone()["n"] == 0
        job = conn.execute(
            "SELECT i.* FROM conversation_message_index i JOIN conversation_messages m USING (message_id) WHERE m.thread_id = 'mine'"
        ).fetchone()
        assert job["indexed_revision"] != job["revision"]


def test_delete_during_inference_cannot_resurrect_thread(database, monkeypatch):
    def embed(text, **kwargs):
        conversations.delete_thread("mine", "user@example.com")
        return [1.0] + [0.0] * 767

    monkeypatch.setattr(search.embeddings, "embed_text", embed)
    assert search.process_due_once()
    with database() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM conversation_chunks").fetchone()["n"] == 0


def test_failures_retry_and_model_changes_reembed(database, monkeypatch):
    def fail(text, **kwargs):
        raise RuntimeError("embedding unavailable")

    monkeypatch.setattr(search.embeddings, "embed_text", fail)
    with pytest.raises(RuntimeError):
        search.process_due_once()
    with database() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM conversation_message_index WHERE attempts = 1 AND next_run_at > NOW()"
            ).fetchone()["n"]
            == 1
        )
        conn.execute("UPDATE conversation_message_index SET next_run_at = NOW()")
    monkeypatch.setattr(search.embeddings, "embed_text", lambda text, **kwargs: [1.0] + [0.0] * 767)
    drain()
    calls = []
    monkeypatch.setattr(search.embeddings, "EMBEDDINGS_MODEL", "different-model")
    monkeypatch.setattr(
        search.embeddings,
        "embed_text",
        lambda text, **kwargs: calls.append(text) or [0.0, 1.0] + [0.0] * 766,
    )
    drain()
    assert len(calls) == 3


def test_new_messages_title_refresh_and_system_exclusion(database, monkeypatch):
    drain()
    with database() as conn:
        conn.execute(
            "UPDATE conversation_threads SET title = 'Greenhouse humidity' WHERE id = 'mine'"
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM conversation_message_index WHERE indexed_revision <> revision"
            ).fetchone()["n"]
            == 1
        )
        conn.execute(
            "INSERT INTO conversation_messages (thread_id, role, content) VALUES ('mine', 'system', 'hidden configuration'), ('mine', 'assistant', 'Water the plants every Saturday')"
        )
    drain()
    with database() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM conversation_chunks c JOIN conversation_messages m USING (message_id) WHERE m.role = 'system'"
            ).fetchone()["n"]
            == 0
        )
    monkeypatch.setattr(search, "query_embedding", lambda query: None)
    assert (
        search.search_messages("user@example.com", "greenhouse", exclude_thread_id="current") == []
    )
    history = conversations.search_threads("user@example.com", "greenhouse")
    assert len(history) == 1 and history[0]["id"] == "mine"
    assert history[0]["matched_message_id"] is None
    hits = search.search_messages("user@example.com", "Saturday", exclude_thread_id="current")
    target = next(hit for hit in hits if hit["role"] == "assistant")
    context = search.get_context("user@example.com", "mine", target["message_id"])
    assert all(message["role"] != "system" for message in context["messages"])


def test_late_matching_passage_is_inspectable_and_expired_lease_recovers(database):
    tail = "The revised seedling plan uses a weekly misting cycle."
    with database() as conn:
        row = conn.execute(
            "INSERT INTO conversation_messages (thread_id, role, content) VALUES ('mine', 'assistant', %s) RETURNING message_id",
            ("Earlier context. " * 1500 + tail,),
        ).fetchone()
        message_id = row["message_id"]
        conn.execute(
            "UPDATE conversation_message_index SET lease_until = NOW() - INTERVAL '1 minute' WHERE message_id = %s",
            (message_id,),
        )
    drain()
    matches = search.search_messages("user@example.com", "seedling", exclude_thread_id="current")
    match = next(hit for hit in matches if hit["message_id"] == message_id)
    assert "weekly misting" in match["snippet"]
    context = search.get_context("user@example.com", "mine", message_id, focus=match["snippet"])
    target = next(message for message in context["messages"] if message["message_id"] == message_id)
    assert target["excerpted"] and tail in target["content"]


def test_long_messages_and_busy_threads_cannot_crowd_out_other_threads(database):
    with database() as conn:
        conn.execute(
            "UPDATE conversation_messages SET content = %s WHERE thread_id = 'mine'",
            ("Shared watering context. " * 1500,),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO conversation_messages (thread_id, role, content) VALUES ('mine', 'assistant', %s)",
                [(f"Shared watering follow-up {i}",) for i in range(60)],
            )
        conn.execute(
            "INSERT INTO conversation_threads (id, user_email, title) VALUES ('separate', 'user@example.com', 'Another discussion')"
        )
        conn.execute(
            "INSERT INTO conversation_messages (thread_id, role, content) VALUES ('separate', 'user', 'Shared watering decision from another discussion')"
        )
    drain()
    for query in ("unmatched paraphrase", "watering"):
        rows = search.search_messages(
            "user@example.com", query, limit=2, per_thread=1, exclude_thread_id="current"
        )
        assert {row["thread_id"] for row in rows} == {"mine", "separate"}
    rows = search.search_messages(
        "user@example.com",
        "unmatched paraphrase",
        limit=2,
        per_thread=1,
        exclude_thread_id="current",
        sort_order="newest",
    )
    with database() as conn:
        latest = conn.execute(
            "SELECT MAX(message_id) AS id FROM conversation_messages WHERE thread_id = 'mine'"
        ).fetchone()["id"]
    assert next(row for row in rows if row["thread_id"] == "mine")["message_id"] == latest
