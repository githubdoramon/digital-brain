# ruff: noqa: F811
"""Read-path integration against disposable PostgreSQL and synthetic evidence."""

from psycopg.types.json import Jsonb

import retrieval
from tests.test_intelligence import database  # noqa: F401
from tools.handlers.memory import handle_get_events, handle_search_memories

OWNER = "reader@example.com"
OTHER = "other@example.com"


def test_knowledge_search_retains_owner_subject_revision_and_evidence(database):
    evidence = [
        {
            "source_ref": "event:review",
            "quote": "Alex owns the Beacon review.",
            "source_at": "2026-06-01T10:00:00Z",
        }
    ]
    with database() as conn:
        conn.execute(
            "INSERT INTO contacts(contact_id,display_name) VALUES('participant-alpha','Alex Carter')"
        )
        for ident, owner, deleted in [
            ("visible", OWNER, False),
            ("private", OTHER, False),
            ("deleted", OWNER, True),
        ]:
            conn.execute(
                """INSERT INTO intelligence_knowledge(knowledge_id,user_email,subject_ref,predicate,statement,status,evidence,inferred,valid_until,deleted_at)
                VALUES(%s,%s,'contact:participant-alpha',%s,'Alex owns the Beacon review.','uncertain',%s,true,'2026-12-31',CASE WHEN %s THEN NOW() END)""",
                (ident, owner, ident, Jsonb(evidence), deleted),
            )
    result = handle_search_memories(
        {"query": "Beacon", "sources": ["knowledge"], "contact_ids": ["participant-alpha"]},
        user_email=OWNER,
    )
    assert result["count"] == 1
    fact = result["results"][0]
    assert fact["id"] == "visible" and fact["kind"] == "knowledge"
    assert fact["evidence"] == evidence and fact["inferred"]
    assert fact["status"] == "uncertain" and fact["subjects"] == ["contact:participant-alpha"]
    assert fact["revision"] == 1 and fact["valid_until"].year == 2026
    assert not handle_search_memories(
        {"query": "Beacon", "sources": ["knowledge"], "contact_ids": ["unrelated"]},
        user_email=OWNER,
    )["results"]
    assert not handle_search_memories({"query": "Beacon", "sources": ["knowledge"]})["results"]


def test_literal_title_recall_ignores_missing_generic_terms(database, monkeypatch):
    with database() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public")
        conn.execute(
            "ALTER TABLE events ADD COLUMN what_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(title,'') || ' ' || coalesce(summary,''))) STORED"
        )
        conn.execute(
            "INSERT INTO events(id,title,summary) VALUES('beacon','Beacon planning','Budget review'),('standup','Weekly standup','Team meeting participants')"
        )
    monkeypatch.setattr(retrieval, "get_conn", database)
    assert not retrieval.bm25_search("Beacon meeting participants")
    hits = retrieval.title_token_search("Beacon meeting participants")
    assert set(hits) == {"beacon"} and hits["beacon"] > 0


def test_event_attendees_use_junction_links_and_stored_emails(database, monkeypatch):
    from datetime import datetime, timezone

    import db

    with database() as conn:
        conn.execute(
            "ALTER TABLE events ADD COLUMN end_date timestamptz, ADD COLUMN tags text[], ADD COLUMN types text[], ADD COLUMN external_id text, ADD COLUMN place_id text"
        )
        conn.execute(
            "ALTER TABLE places ADD COLUMN city text, ADD COLUMN country text, ADD COLUMN lat float8, ADD COLUMN lon float8"
        )
        conn.execute(
            "CREATE TABLE event_contacts(event_id text,contact_id text,PRIMARY KEY(event_id,contact_id))"
        )
        conn.execute(
            "INSERT INTO contacts(contact_id,display_name,emails) VALUES('self','Morgan Brooks',ARRAY[%s]),('participant-alpha','Alex Carter',ARRAY['alex@example.com'])",
            (OWNER,),
        )
        conn.execute(
            "INSERT INTO events(id,title,start_date) VALUES('beacon','Beacon planning',%s)",
            (datetime(2026, 6, 1, 10, tzinfo=timezone.utc),),
        )
        conn.execute(
            "INSERT INTO event_contacts VALUES('beacon','participant-alpha'),('beacon','self')"
        )
    monkeypatch.setattr(db, "get_conn", database)
    monkeypatch.setattr("tools.handlers.memory._queue_retrieved_knowledge", lambda *a: None)
    result = handle_get_events(
        {
            "action": "by_time_span",
            "time_start": "2026-06-01T00:00:00Z",
            "time_end": "2026-06-01T23:59:59Z",
        },
        user_email=OWNER,
    )
    people = {person["contact_id"]: person for person in result["events"][0]["people"]}
    assert (
        people["participant-alpha"]["emails"] == ["alex@example.com"]
        and not people["participant-alpha"]["is_self"]
    )
    assert people["self"]["is_self"]
