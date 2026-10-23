"""Real PostgreSQL contracts; model responses use anonymous deterministic fixtures."""

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

import intelligence as store
import intelligence_jobs as jobs
import intelligence_model as model
import messaging_archive as archive

OWNER = "reader@example.com"
OTHER = "other@example.com"
FUTURE = datetime(2100, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def database(monkeypatch, request):
    dsn = os.getenv("MESSAGING_TEST_DSN")
    if not dsn:
        pytest.skip("MESSAGING_TEST_DSN must identify a disposable PostgreSQL instance")
    schema = "intelligence_test_" + uuid4().hex

    @contextmanager
    def connect():
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            yield conn

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        with connect() as conn:
            conn.execute("""
                CREATE TABLE conversation_threads(id TEXT PRIMARY KEY,user_email TEXT);
                CREATE TABLE conversation_messages(message_id BIGSERIAL PRIMARY KEY,thread_id TEXT REFERENCES conversation_threads,
                    role TEXT,content TEXT,created_at TIMESTAMPTZ DEFAULT NOW(),metadata JSONB DEFAULT '{}');
                CREATE TABLE user_facts(fact_id TEXT PRIMARY KEY,user_email TEXT,content TEXT,category TEXT,
                    importance SMALLINT,fact_mode TEXT DEFAULT 'soft',rule_type TEXT,rule_scope TEXT[] DEFAULT '{}',
                    rule_payload JSONB DEFAULT '{}',source_thread_id TEXT,created_at TIMESTAMPTZ DEFAULT NOW(),updated_at TIMESTAMPTZ DEFAULT NOW());
                CREATE TABLE places(place_id TEXT PRIMARY KEY,name TEXT);
                CREATE TABLE contacts(contact_id TEXT PRIMARY KEY, display_name TEXT, phones TEXT[],
                    aliases TEXT[] DEFAULT '{}', birthday DATE, tags TEXT[] DEFAULT '{}', comments TEXT, emails TEXT[] DEFAULT '{}',
                    links TEXT[] DEFAULT '{}', external_id TEXT, comments_embed FLOAT8[]);
                CREATE TABLE contact_relationships(relationship_id TEXT PRIMARY KEY,
                    from_contact_id TEXT REFERENCES contacts ON DELETE CASCADE,to_contact_id TEXT REFERENCES contacts ON DELETE CASCADE,
                    relationship_type TEXT,reciprocal_type TEXT,created_at TIMESTAMPTZ DEFAULT NOW(),updated_at TIMESTAMPTZ DEFAULT NOW());
                CREATE TABLE events(id TEXT PRIMARY KEY, title TEXT, summary TEXT, start_date TIMESTAMPTZ,
                    is_cancelled BOOLEAN DEFAULT FALSE);
                CREATE TABLE documents(document_id TEXT PRIMARY KEY, title TEXT, description TEXT, content TEXT,
                    document_date TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW());
                CREATE TABLE todos(todo_id TEXT PRIMARY KEY, description TEXT, status TEXT DEFAULT 'pending',
                    due_date DATE, created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW());
                CREATE TABLE todo_contacts(todo_id TEXT REFERENCES todos, contact_id TEXT REFERENCES contacts,
                    PRIMARY KEY(todo_id, contact_id));
                INSERT INTO contacts(contact_id, display_name) VALUES ('alex', 'Alex Carter');
            """)
            root = Path(__file__).resolve().parents[1] / "db_migrations"
            for name in (
                "0073_messaging_archive.sql",
                "0074_telegram_archive.sql",
                "0075_whatsapp_history_requests.sql",
                "0076_scheduled_intelligence.sql",
                "0077_intelligence_failures.sql",
                "0078_messaging_source_links.sql",
                "0079_shared_knowledge.sql",
                "0080_learned_contact_changes.sql",
                "0081_learning_reliability.sql",
                "0082_intelligence_context_recovery.sql",
                "0083_learning_validation_recovery.sql",
                "0084_learning_progress.sql",
                "0085_learning_fairness_note_dedup.sql",
                "0086_learning_efficiency.sql",
                "0087_background_learning_recovery.sql",
                "0088_command_learning_ownership.sql",
                "0089_learning_scope.sql",
                "0090_learning_evidence_recovery.sql",
            ):
                if (
                    name == "0088_command_learning_ownership.sql"
                    and getattr(request, "param", None) == "pre_command_ownership"
                ):
                    continue
                if name in {
                    "0086_learning_efficiency.sql",
                    "0087_background_learning_recovery.sql",
                    "0089_learning_scope.sql",
                    "0090_learning_evidence_recovery.sql",
                } and getattr(request, "param", None) in {
                    "pre_efficiency",
                    "pre_fairness",
                    "pre_context",
                    "pre_reliability",
                }:
                    continue
                if (
                    name == "0085_learning_fairness_note_dedup.sql"
                    and getattr(request, "param", None) == "pre_fairness"
                ):
                    continue
                if (
                    name == "0082_intelligence_context_recovery.sql"
                    and getattr(request, "param", None) == "pre_context"
                ):
                    continue
                if (
                    name == "0081_learning_reliability.sql"
                    and getattr(request, "param", None) == "pre_reliability"
                ):
                    continue
                if (
                    name == "0079_shared_knowledge.sql"
                    and getattr(request, "param", None) == "legacy"
                ):
                    conn.execute(
                        "INSERT INTO user_facts(fact_id,user_email,content,category,importance,source_thread_id) VALUES('uf_old',%s,'Prefers quiet keyboards','preference',7,'old-thread')",
                        (OWNER,),
                    )
                    conn.execute(
                        "INSERT INTO user_facts(fact_id,user_email,content,category,importance,fact_mode,rule_type) VALUES('rule_old',%s,'Interpret Alex as Alex Carter','general',8,'hard_rule','entity_alias')",
                        (OWNER,),
                    )
                conn.execute((root / name).read_text())
        import knowledge
        import knowledge_jobs
        import learning_progress

        for module in (store, jobs, archive, knowledge, knowledge_jobs, learning_progress):
            monkeypatch.setattr(module, "get_conn", connect)
        monkeypatch.setattr(knowledge, "vector_for", lambda text: None)
        jobs._STOP.clear()
        yield connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def setup_owner(database, owner=OWNER):
    store.initialize(owner)
    with database() as conn:
        conn.execute(
            "UPDATE intelligence_state SET started_at='2026-01-01' WHERE user_email=%s", (owner,)
        )
    # Production startup enqueues the latest scheduled slot; history is now owned
    # by knowledge_learning rather than an implicit intelligence backlog run.
    jobs.enqueue_due(owner, now=FUTURE)


def ingest(
    database,
    *,
    body="I sent the full quote to Alex today.",
    ident="1",
    owner=OWNER,
    sent="2026-01-03T12:00:00+00:00",
    outgoing=True,
    forwarded=False,
    kind="message",
):
    a = archive.set_connection(owner, True)
    archive.stage_records(
        a["account_id"],
        a["generation"],
        [
            {
                "kind": kind,
                "origin": "live",
                "chat": "15555550101@s.whatsapp.net",
                "sender": "15555550100@s.whatsapp.net"
                if outgoing
                else "15555550101@s.whatsapp.net",
                "self_jid": "15555550100@s.whatsapp.net",
                "id": ident,
                "sent_at": sent,
                "effective_at": sent,
                "outgoing": outgoing,
                "name": "Alex Carter",
                "title": "",
                "body": body,
                "attachments": [],
                "reply_id": None,
                "forwarded": forwarded,
            }
        ],
    )
    archive.drain_once()


def run(database, owner=OWNER):
    ident = uuid4().hex
    with database() as conn:
        conn.execute(
            """INSERT INTO intelligence_runs(run_id, user_email, slot, cutoff, lease_token, lease_until)
            VALUES (%s,%s,%s,%s,'token', NOW() + INTERVAL '1 hour')""",
            (ident, owner, ident, FUTURE),
        )
    return {"run_id": ident, "user_email": owner, "lease_token": "token", "cutoff": FUTURE}


def finding(row, *, kind="knowledge", **kwargs):
    return dict(
        kind=kind,
        statement="Alex received the quote",
        subject_ref="self",
        predicate="quote_delivery",
        status="current",
        applicable_at=None,
        valid_from=None,
        valid_until=None,
        context="work",
        existing_id=None,
        assignee_kind="user",
        assignee_ref=None,
        project_id=None,
        due_date=None,
        reason="The sender confirmed delivery.",
        explicit_completion=kind == "complete",
        requested_status="pending",
        evidence=[{"ref": row["ref"], "quote": row["text"]}],
        **kwargs,
    )


def context(batch):
    return {
        "people": [
            {
                "identity_id": r["payload"]["identity_id"],
                "contact_id": None,
                "is_self": True,
                "display_name": "Alex Carter",
            }
            for r in batch
            if r["kind"] == "message"
        ],
        "contacts": [],
        "knowledge": [],
        "tasks": [],
        "projects": [],
        "proposals": [],
    }


def test_calendar_slots_observe_local_daylight_saving():
    assert jobs.latest_slot(datetime(2026, 7, 1, 4, tzinfo=timezone.utc)).hour == 5
    assert jobs.latest_slot(datetime(2026, 1, 1, 4, tzinfo=timezone.utc)).hour == 19
    assert jobs.latest_slot(datetime(2026, 7, 1, 18, tzinfo=timezone.utc)).hour == 19
    assert jobs.latest_slot(datetime(2026, 1, 1, 19, tzinfo=timezone.utc)).hour == 19


def test_seeded_backlog_and_late_old_imports_cannot_produce_tasks(database):
    ingest(database)
    store.initialize(OWNER)
    batch = store.next_batch(OWNER, FUTURE, knowledge_only=True)
    assert batch and not any(r["actionable"] for r in batch)
    assert (
        store.validate_findings(
            {"findings": [finding(batch[0], kind="task")]}, batch, context(batch)
        )
        == []
    )
    setup_owner(database)
    ingest(database, ident="old", sent="2025-12-30T12:00:00+00:00")
    batch = store.next_batch(OWNER, FUTURE, knowledge_only=True)
    old = next(r for r in batch if "2025" in r["payload"]["sent_at"])
    assert not old["actionable"]


def test_cross_source_capture_chunk_receipts_and_owner_isolation(database):
    setup_owner(database)
    setup_owner(database, OTHER)
    ingest(database)
    ingest(database, owner=OTHER)
    with database() as conn:
        conn.execute(
            "INSERT INTO events VALUES ('event1','Sample decision','The quote was approved.','2026-01-03',FALSE)"
        )
        conn.execute(
            "INSERT INTO documents(document_id,title,content) VALUES ('doc1','Sample document',%s)",
            ("A" * 13000,),
        )
    batch = store.next_batch(OWNER, FUTURE)
    assert {r["kind"] for r in batch} == {"message", "event", "document"}
    assert all(r["owner_email"] in (None, OWNER) for r in batch)
    docs = [r for r in batch if r["kind"] == "document"]
    assert len(docs) == 3
    store.apply_batch(OWNER, run(database), batch, context(batch), [])
    assert store.next_batch(OWNER, FUTURE) == []
    assert store.next_batch(OTHER, FUTURE)


def test_source_revert_and_concurrent_late_commit_are_not_skipped(database):
    setup_owner(database)
    with database() as conn:
        conn.execute("INSERT INTO events VALUES ('event1','First','A decision','2026-01-03',FALSE)")
    first = store.next_batch(OWNER, FUTURE)
    store.apply_batch(OWNER, run(database), first, context(first), [])
    with database() as conn:
        conn.execute("UPDATE events SET title='Second' WHERE id='event1'")
        conn.execute("UPDATE events SET title='First' WHERE id='event1'")
    assert len(store.next_batch(OWNER, FUTURE)) == 2


def test_source_changes_during_inference_reject_effects_but_preserve_new_work(database):
    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    f = finding(batch[0])
    findings = store.validate_findings({"findings": [f]}, batch, context(batch))
    with database() as conn:
        conn.execute("UPDATE messaging_messages SET body='Correction: the quote is not sent.'")
    store.apply_batch(OWNER, run(database), batch, context(batch), findings)
    assert store.knowledge_for(OWNER) == []
    assert store.next_batch(OWNER, FUTURE)


def test_evidence_and_targets_must_exist_in_scoped_context(database):
    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    f = finding(batch[0])
    f["evidence"][0]["quote"] = "invented evidence"
    with pytest.raises(ValueError, match="invalid_evidence"):
        store.validate_findings({"findings": [f]}, batch, context(batch))
    f = finding(batch[0])
    f["subject_ref"] = "contact:not-confirmed"
    with pytest.raises(ValueError, match="invalid_subject"):
        store.validate_findings({"findings": [f]}, batch, context(batch))


def test_knowledge_revisions_manual_protection_and_identity_linking(database):
    setup_owner(database)
    ingest(database, outgoing=False)
    batch = store.next_batch(OWNER, FUTURE)
    ctx = context(batch)
    f = finding(batch[0])
    f["subject_ref"] = "identity:" + batch[0]["payload"]["identity_id"]
    findings = store.validate_findings({"findings": [f]}, batch, ctx)
    store.apply_batch(OWNER, run(database), batch, ctx, findings)
    assert len(store.knowledge_for(OWNER)) == 1
    assert store.knowledge_for(OWNER, contact_id="alex") == []
    with database() as conn:
        conn.execute("UPDATE messaging_identities SET contact_id='alex'")
    assert len(store.knowledge_for(OWNER, contact_id="alex")) == 1
    with database() as conn:
        conn.execute("UPDATE intelligence_knowledge SET statement='Manual correction', manual=TRUE")
    store.apply_batch(OWNER, run(database), batch, ctx, findings)
    assert store.knowledge_for(OWNER)[0]["statement"] == "Manual correction"
    assert store.knowledge_for(OTHER) == []


def test_knowledge_revision_serializes_datetimes(database):
    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    f = finding(batch[0])
    fs = store.validate_findings({"findings": [f]}, batch, context(batch))
    store.apply_batch(OWNER, run(database), batch, context(batch), fs)
    fs[0]["statement"] = "The quote is delivered"
    store.apply_batch(OWNER, run(database), batch, context(batch), fs)
    with database() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM intelligence_knowledge_revisions").fetchone()[
                "n"
            ]
            == 1
        )


@pytest.mark.parametrize(
    "forwarded,outgoing,manual,fresh,verified,expected",
    [
        (False, True, False, True, True, "completed"),
        (True, True, False, True, True, "pending"),
        (False, False, False, True, True, "pending"),
        (False, True, True, True, True, "pending"),
        (False, True, False, False, True, "pending"),
        (False, True, False, True, False, "pending"),
    ],
)
def test_completion_requires_direct_actor_fresh_version_and_review(
    database, forwarded, outgoing, manual, fresh, verified, expected
):
    setup_owner(database)
    ingest(database, forwarded=forwarded, outgoing=outgoing)
    with database() as conn:
        conn.execute(
            """INSERT INTO todos(todo_id, description, created_at, intelligence_manual_at)
            VALUES ('task1','Send the quote to Alex','2026-01-02',%s)""",
            (store.utcnow() if manual else None,),
        )
    batch = store.next_batch(OWNER, FUTURE)
    ctx = store.context_for(OWNER, batch)
    assert ctx["tasks"]
    f = finding(batch[0], kind="complete")
    f["existing_id"] = "task1"
    fs = store.validate_findings({"findings": [f]}, batch, ctx)
    fs[0]["verified_completion"] = verified
    if not fresh:
        with database() as conn:
            conn.execute("UPDATE todos SET updated_at=NOW() + INTERVAL '1 second'")
    store.apply_batch(OWNER, run(database), batch, ctx, fs)
    with database() as conn:
        assert conn.execute("SELECT status FROM todos").fetchone()["status"] == expected
        assert conn.execute("SELECT count(*) AS n FROM intelligence_task_evidence").fetchone()[
            "n"
        ] == (1 if expected == "completed" else 0)


def test_proposal_acceptance_dismissal_and_replay_are_atomic(database):
    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    ctx = context(batch)
    f = finding(batch[0], kind="task")
    fs = store.validate_findings({"findings": [f]}, batch, ctx)
    store.apply_batch(OWNER, run(database), batch, ctx, fs)
    p = store.list_proposals(OWNER)[0]
    assert store.resolve_proposal(OTHER, p["proposal_id"], accept=True) is None
    assert store.resolve_proposal(OWNER, p["proposal_id"], accept=True)["accepted"]
    assert store.resolve_proposal(OWNER, p["proposal_id"], accept=True)["accepted"]
    store.apply_batch(OWNER, run(database), batch, ctx, fs)
    assert not store.list_proposals(OWNER)
    with database() as conn:
        assert conn.execute("SELECT count(*) AS n FROM todos").fetchone()["n"] == 1


def test_stale_proposal_cannot_be_accepted(database):
    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    ctx = context(batch)
    fs = store.validate_findings({"findings": [finding(batch[0], kind="project")]}, batch, ctx)
    store.apply_batch(OWNER, run(database), batch, ctx, fs)
    p = store.list_proposals(OWNER)[0]
    with database() as conn:
        conn.execute("UPDATE messaging_messages SET body='This project was cancelled.'")
    assert (
        store.resolve_proposal(OWNER, p["proposal_id"], accept=True)["reason"] == "source_changed"
    )


def test_leases_recover_and_serialize_runs_per_owner(database):
    setup_owner(database)
    claimed = jobs.claim_run({OWNER})
    assert claimed
    assert jobs.claim_run({OWNER}) is None
    with database() as conn:
        conn.execute("UPDATE intelligence_runs SET lease_until=NOW()-INTERVAL '1 second'")
    recovered = jobs.claim_run({OWNER})
    assert recovered["lease_token"] != claimed["lease_token"]
    with pytest.raises(ValueError, match="lease_expired"):
        store.apply_batch(OWNER, claimed, [], {}, [])


def test_failed_inference_keeps_receipts_unadvanced_and_uses_safe_error(database, monkeypatch):
    setup_owner(database)
    ingest(database)
    # Make the backlog eligible for this controlled new source as well.
    with database() as conn:
        conn.execute("UPDATE intelligence_runs SET knowledge_only=FALSE, cutoff=%s", (FUTURE,))

    def fail(*a, **k):
        raise RuntimeError("private source text must not be persisted as an error")

    monkeypatch.setattr(model, "analyze", fail)
    assert jobs.process_once({OWNER})
    assert store.next_batch(OWNER, FUTURE)
    with database() as conn:
        row = conn.execute("SELECT status,error_code FROM intelligence_runs").fetchone()
        assert row == {"status": "failed", "error_code": "analysis_failed"}


def test_structured_model_reconciles_candidates_and_separately_reviews_completion(
    database, monkeypatch
):
    setup_owner(database)
    ingest(database)
    with database() as conn:
        conn.execute(
            "INSERT INTO todos(todo_id,description,created_at) VALUES ('task1','Send quote to Alex','2026-01-02')"
        )
    batch = store.next_batch(OWNER, FUTURE)
    f = finding(batch[0], kind="complete")
    f["existing_id"] = "task1"
    replies = iter([{"findings": [f]}, {"findings": [f]}, {"completed": True}])
    monkeypatch.setattr(model, "call_llm_json", lambda *a, **k: next(replies))
    ctx, fs = model.analyze(OWNER, batch)
    assert fs[0]["verified_completion"] is True
    assert ctx["tasks"][0]["todo_id"] == "task1"


@pytest.mark.parametrize("defect", ["quote", "status", "null_status", "predicate"])
def test_model_repairs_rejected_facts_with_bounded_private_feedback(database, monkeypatch, defect):
    import copy
    import json

    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    valid = finding(batch[0])
    bad = copy.deepcopy(valid)
    if defect == "quote":
        bad["evidence"][0]["quote"] = "I sent the complete quote to Alex today."
    elif defect == "predicate":
        bad["predicate"] = "café.delivery"
    else:
        bad["status"] = "open" if defect == "status" else None
    # Bad evidence must be repaired before the reconciliation request.
    replies = iter([{"findings": [bad]}, {"findings": [valid]}, {"findings": [valid]}])
    calls = []

    def ask(prompt, **kwargs):
        calls.append((json.loads(prompt), kwargs))
        return next(replies)

    monkeypatch.setattr(model, "call_llm_json", ask)
    ctx, fs = model.analyze(OWNER, batch)
    assert len(fs) == 1
    assert fs[0]["evidence"][0]["quote"] == batch[0]["text"]
    assert all(kwargs["sensitive_payload"] for _, kwargs in calls)
    assert calls[-1][0]["validation_feedback"]["code"] in {"invalid_schema", "invalid_evidence"}
    store.apply_batch(OWNER, run(database), batch, ctx, fs)
    assert len(store.knowledge_for(OWNER)) == 1


def test_invalid_model_output_stops_after_one_repair_without_leaking_validation_input(
    database, monkeypatch
):
    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    calls = []

    def ask(*args, **kwargs):
        calls.append(kwargs)
        return {"findings": [{"statement": "PRIVATE_INVALID_MODEL_TEXT"}]}

    monkeypatch.setattr(model, "call_llm_json", ask)
    with pytest.raises(model.InvalidModelOutput, match=r"^invalid_schema$") as error:
        model.analyze(OWNER, batch)
    assert len(calls) == 2
    assert "PRIVATE_INVALID_MODEL_TEXT" not in str(error.value)


def test_model_cites_controller_supplied_spans_without_transcribing_quotes(database, monkeypatch):
    import json

    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    candidate = finding(batch[0])
    candidate["evidence"] = [{"ref": batch[0]["ref"], "span_index": 0, "quote": None}]

    def ask(prompt, **kwargs):
        sources = json.loads(prompt)["sources"]
        assert sources[0]["text_spans"] == [batch[0]["text"]]
        assert "text" not in sources[0]
        return {"findings": [candidate]}

    monkeypatch.setattr(model, "call_llm_json", ask)
    ctx, fs = model.analyze(OWNER, batch)
    assert fs[0]["evidence"][0]["quote"] == batch[0]["text"]
    store.apply_batch(OWNER, run(database), batch, ctx, fs)
    assert len(store.knowledge_for(OWNER)) == 1


def test_subject_repair_receives_rejected_ref_and_commits_supported_result(database, monkeypatch):
    import json

    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    valid = finding(batch[0])
    invalid = {**valid, "subject_ref": "contact:missing"}
    responses = iter(
        [
            {"findings": [invalid]},
            {"findings": [invalid]},
            {"findings": [valid]},
            {"findings": [valid]},
        ]
    )
    calls = []

    def ask(prompt, **kwargs):
        calls.append(json.loads(prompt))
        return next(responses)

    monkeypatch.setattr(model, "call_llm_json", ask)
    ctx, fs = model.analyze(OWNER, batch, knowledge_only=True)
    feedback = calls[2]["validation_feedback"]
    assert feedback["code"] == "invalid_subject"
    assert feedback["details"]["rejected_refs"] == ["contact:missing"]
    assert "self" in feedback["details"]["allowed_refs"]
    store.apply_batch(OWNER, run(database), batch, ctx, fs)
    assert len(store.knowledge_for(OWNER)) == 1


def test_reconciliation_groups_candidates_with_only_their_cited_source_text(database, monkeypatch):
    import json

    setup_owner(database)
    ingest(database, body="An anonymous source sentence. " * 160)
    batch = store.next_batch(OWNER, FUTURE)
    candidates = [
        {
            **finding(batch[0]),
            "predicate": f"observation_{index}",
            "evidence": [{"ref": batch[0]["ref"], "span_index": 0, "quote": None}],
        }
        for index in range(7)
    ]
    requests = []

    def ask(prompt, **kwargs):
        payload = json.loads(prompt)
        requests.append(payload)
        if "candidates" not in payload:
            assert not payload["context"]["knowledge"]
            assert not payload["context"]["tasks"]
            return {"findings": candidates}
        assert len(payload["candidates"]) <= store.MAX_FINDINGS
        assert payload["sources"][0]["text_spans"][1:] == [None] * (
            len(model.source_spans(batch[0]["text"])) - 1
        )
        return {"findings": payload["candidates"]}

    monkeypatch.setattr(model, "call_llm_json", ask)
    _, findings = model.analyze(OWNER, batch, knowledge_only=True)
    assert len(findings) == 7
    assert [len(request["candidates"]) for request in requests[1:]] == [7]


def test_rejected_batches_defer_without_receipts_then_pause_and_allow_other_sources(
    database, monkeypatch
):
    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    with database() as conn:
        conn.execute("UPDATE intelligence_runs SET knowledge_only=FALSE, cutoff=%s", (FUTURE,))
    claimed = jobs.claim_run({OWNER})
    for attempt in range(1, 4):
        assert store.next_batch(OWNER, FUTURE)
        store.defer_batch(OWNER, claimed, batch, "invalid_evidence")
        assert store.next_batch(OWNER, FUTURE) == []
        with database() as conn:
            failed = conn.execute("SELECT * FROM intelligence_failures").fetchone()
            assert failed["attempts"] == attempt
            assert (failed["next_attempt_at"] is None) == (attempt == 3)
            assert not conn.execute("SELECT 1 FROM intelligence_processed").fetchone()
            if attempt < 3:
                conn.execute("UPDATE intelligence_failures SET next_attempt_at=NOW()")
    ingest(database, ident="healthy", body="I have moved to Lisbon permanently.")
    assert [r["payload"]["text"] for r in store.next_batch(OWNER, FUTURE)] == [
        "I have moved to Lisbon permanently."
    ]
    monkeypatch.setattr(model, "analyze", lambda *a, **k: ({}, []))
    jobs._process_run(claimed)
    # A committed batch yields; a later turn performs the final drain/review.
    with database() as conn:
        conn.execute("UPDATE intelligence_runs SET next_attempt_at=NOW()")
    jobs.process_once({OWNER})
    with database() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM intelligence_processed").fetchone()["n"] == 1
        assert (
            conn.execute("SELECT status FROM intelligence_runs").fetchone()["status"] == "succeeded"
        )
        conn.execute("UPDATE intelligence_failures SET next_attempt_at=NOW()")
    jobs.requeue_deferred(OWNER)
    recovered = jobs.claim_run({OWNER})
    assert recovered
    retry_batch = store.next_batch(OWNER, FUTURE)
    assert [r["ref"] for r in retry_batch] == [batch[0]["ref"]]
    store.apply_batch(OWNER, recovered, retry_batch, {}, [])
    with database() as conn:
        assert not conn.execute("SELECT 1 FROM intelligence_failures").fetchone()


def test_worker_continues_past_invalid_batch_without_saving_it(database, monkeypatch, caplog):
    setup_owner(database)
    ingest(database)
    with database() as conn:
        conn.execute("UPDATE intelligence_runs SET knowledge_only=FALSE, cutoff=%s", (FUTURE,))
    calls = []

    def reject(*args, **kwargs):
        calls.append(1)
        raise model.InvalidModelOutput("invalid_evidence")

    monkeypatch.setattr(model, "analyze", reject)
    assert jobs.process_once({OWNER})
    assert len(calls) == 1
    with database() as conn:
        conn.execute("UPDATE intelligence_runs SET next_attempt_at=NOW()")
    assert jobs.process_once({OWNER})
    with database() as conn:
        assert not conn.execute("SELECT 1 FROM intelligence_processed").fetchone()
        assert (
            conn.execute("SELECT attempts FROM intelligence_failures").fetchone()["attempts"] == 1
        )
        assert (
            conn.execute("SELECT status FROM intelligence_runs").fetchone()["status"] == "succeeded"
        )
    assert "reason=invalid_evidence" in caplog.text


def test_http_rejected_batch_does_not_block_later_sources(database, monkeypatch):
    from llm_policy import LLMRequestRejected

    setup_owner(database)
    ingest(database)
    ingest(database, ident="healthy", body="The quote was received this morning.")
    monkeypatch.setattr(store, "BATCH_SIZE", 1)
    first = store.next_batch(OWNER, FUTURE)[0]["source_id"]
    with database() as conn:
        conn.execute("UPDATE intelligence_runs SET cutoff=%s,knowledge_only=FALSE", (FUTURE,))

    def analyze(owner, batch, **kwargs):
        if batch[0]["source_id"] == first:
            raise LLMRequestRejected("invalid_request")
        return {}, []

    monkeypatch.setattr(model, "analyze", analyze)
    assert jobs.process_once({OWNER})
    # Rejected and healthy batches get separate turns, leaving room for history.
    for _ in range(2):
        with database() as conn:
            conn.execute("UPDATE intelligence_runs SET next_attempt_at=NOW()")
        assert jobs.process_once({OWNER})
    with database() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM intelligence_processed").fetchone()["n"] == 1
        assert (
            conn.execute("SELECT error_code FROM intelligence_failures").fetchone()["error_code"]
            == "invalid_request"
        )
        assert (
            conn.execute("SELECT status FROM intelligence_runs").fetchone()["status"] == "succeeded"
        )


def test_committed_progress_counts_actual_effects_and_redacts_content(database, caplog):
    import logging

    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    ctx = context(batch)
    fs = store.validate_findings({"findings": [finding(batch[0])]}, batch, ctx)
    active = run(database)
    caplog.set_level(logging.INFO)
    caplog.clear()
    created = store.apply_batch(OWNER, active, batch, ctx, fs)
    assert created["facts_created"] == 1
    assert created["facts_updated"] == 0
    assert created["parts_processed"] == 1
    repeated = store.apply_batch(OWNER, active, batch, ctx, fs)
    assert repeated["facts_created"] == 0
    assert repeated["facts_updated"] == 1
    assert repeated["parts_processed"] == 0
    with database() as conn:
        conn.execute("UPDATE intelligence_knowledge SET manual=TRUE")
    protected = store.apply_batch(OWNER, active, batch, ctx, fs)
    assert protected["facts_protected"] == 1
    assert protected["facts_updated"] == 0
    assert "batch_committed" in caplog.text
    assert "facts_created=1" in caplog.text
    for private in (OWNER, fs[0]["statement"], batch[0]["text"], batch[0]["entity_id"]):
        assert private not in caplog.text


def test_rolled_back_effects_never_log_a_successful_commit(database, monkeypatch, caplog):
    import logging

    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    ctx = context(batch)
    fs = store.validate_findings({"findings": [finding(batch[0])]}, batch, ctx)
    save = store._save_knowledge

    def abort(cur, owner, f):
        save(cur, owner, f)
        raise RuntimeError("simulated transaction failure")

    monkeypatch.setattr(store, "_save_knowledge", abort)
    caplog.set_level(logging.INFO)
    caplog.clear()
    with pytest.raises(RuntimeError, match="simulated transaction failure"):
        store.apply_batch(OWNER, run(database), batch, ctx, fs)
    assert "batch_committed" not in caplog.text
    with database() as conn:
        assert not conn.execute("SELECT 1 FROM intelligence_knowledge").fetchone()
        assert not conn.execute("SELECT 1 FROM intelligence_processed").fetchone()


def test_progress_counts_parts_by_owner_mode_and_deferred_state(database, caplog):
    import logging

    setup_owner(database)
    setup_owner(database, OTHER)
    ingest(database, body="A" * 6000)
    ingest(database, ident="old", sent="2025-12-01T12:00:00+00:00")
    ingest(database, owner=OTHER, ident="private")
    batch = store.next_batch(OWNER, FUTURE)
    assert len(batch) == 2
    active = {**run(database), "knowledge_only": False}
    store.defer_batch(OWNER, active, batch[:1], "invalid_evidence")
    counts = store.progress(OWNER, FUTURE)
    assert counts["total_parts"] == 2
    assert counts["remaining_parts"] == 2
    assert counts["ready_parts"] == counts["retrying_parts"] == 1
    with database() as conn:
        conn.execute("UPDATE intelligence_failures SET attempts=3,next_attempt_at=NULL")
    store.apply_batch(OWNER, active, batch[1:], {}, [])
    counts = store.progress(OWNER, FUTURE)
    assert counts["processed_parts"] == counts["remaining_parts"] == counts["paused_parts"] == 1
    assert counts["ready_parts"] == counts["retrying_parts"] == 0
    assert store.progress(OWNER, FUTURE, knowledge_only=True)["total_parts"] == 1
    caplog.set_level(logging.INFO)
    caplog.clear()
    jobs.finish_run(active)
    assert "run_drained mode=scheduled" in caplog.text
    assert "remaining_parts=1" in caplog.text
    assert "paused_parts=1" in caplog.text
    assert OWNER not in caplog.text


def test_duplicate_proposals_are_not_counted_as_creations(database):
    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    ctx = context(batch)
    fs = store.validate_findings({"findings": [finding(batch[0], kind="task")]}, batch, ctx)
    active = run(database)
    assert store.apply_batch(OWNER, active, batch, ctx, fs)["proposals_created"] == 1
    duplicate = store.apply_batch(OWNER, active, batch, ctx, fs)
    assert duplicate["proposals_created"] == 0
    assert duplicate["findings_skipped"] == 1


def test_failed_history_waits_in_canonical_queue_and_retries_when_due(database, monkeypatch):
    import knowledge_jobs

    setup_owner(database)
    ingest(database, sent="2025-12-01T12:00:00+00:00")
    knowledge_jobs.initialize_owner(OWNER)
    monkeypatch.setattr(
        model,
        "analyze",
        lambda *a, **k: (_ for _ in ()).throw(model.InvalidModelOutput("invalid_evidence")),
    )
    assert knowledge_jobs.process_once([OWNER])
    assert not knowledge_jobs.process_once([OWNER])
    with database() as conn:
        assert (
            conn.execute("SELECT status FROM knowledge_learning").fetchone()["status"] == "failed"
        )
        conn.execute("UPDATE knowledge_learning SET next_attempt_at=NOW()")
    monkeypatch.setattr(model, "analyze", lambda *a, **k: ({}, []))
    assert knowledge_jobs.process_once([OWNER])
    assert not knowledge_jobs.process_once([OWNER])


def test_failure_status_and_explicit_retry_are_private_and_owner_scoped(database, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import intelligence as routes
    from routes import knowledge as knowledge_routes

    setup_owner(database)
    setup_owner(database, OTHER)
    ingest(database)
    with database() as conn:
        conn.execute(
            """INSERT INTO intelligence_failures
            (user_email,source_id,part,knowledge_only,attempts,error_code)
            SELECT %s,source_id,0,FALSE,3,'invalid_evidence' FROM intelligence_sources""",
            (OWNER,),
        )
    monkeypatch.setattr(routes, "get_conn", database)
    app = FastAPI()
    app.include_router(routes.create_intelligence_router())
    app.include_router(knowledge_routes.create_knowledge_router())
    app.dependency_overrides[routes.get_current_user] = lambda: {"email": OTHER}
    app.dependency_overrides[knowledge_routes.get_current_user] = lambda: {"email": OTHER}
    with TestClient(app) as client:
        assert client.get("/mobile/intelligence/status").json()["failures"] == {
            "paused": 0,
            "retrying": 0,
        }
        assert client.post("/mobile/intelligence/retry-failures").json() == {"retrying": 0}
        app.dependency_overrides[routes.get_current_user] = lambda: {"email": OWNER}
        app.dependency_overrides[knowledge_routes.get_current_user] = lambda: {"email": OWNER}
        status = client.get("/mobile/intelligence/status")
        assert status.headers["cache-control"] == "no-store"
        assert status.json()["failures"] == {"paused": 1, "retrying": 0}
        assert client.post("/mobile/intelligence/retry-failures").json() == {"retrying": 1}
        assert client.get("/mobile/intelligence/status").json()["failures"] == {
            "paused": 0,
            "retrying": 1,
        }
    with database() as conn:
        assert (
            conn.execute("SELECT attempts FROM intelligence_failures").fetchone()["attempts"] == 0
        )


def test_transactions_committed_out_of_sequence_still_get_processed(database):
    setup_owner(database)
    with database() as delayed:
        delayed.execute(
            "INSERT INTO events VALUES ('early','Early source','Earlier allocation','2026-01-03',FALSE)"
        )
        with database() as committed:
            committed.execute(
                "INSERT INTO events VALUES ('later','Later source','Committed first','2026-01-03',FALSE)"
            )
        batch = store.next_batch(OWNER, FUTURE)
        assert [r["entity_id"] for r in batch] == ["later"]
        store.apply_batch(OWNER, run(database), batch, context(batch), [])
    assert [r["entity_id"] for r in store.next_batch(OWNER, FUTURE)] == ["early"]


def test_slots_dedupe_and_scheduled_work_precedes_backlog(database):
    setup_owner(database)
    now = store.utcnow()
    assert jobs.enqueue_due(OWNER, now=now)
    assert not jobs.enqueue_due(OWNER, now=now)
    claimed = jobs.claim_run({OWNER})
    assert not claimed["knowledge_only"]


def test_broadcast_and_announcement_sources_never_reach_the_model(database, monkeypatch):
    setup_owner(database)
    ingest(database)
    with database() as conn:
        conn.execute("UPDATE messaging_conversations SET kind='channel'")
        conn.execute("UPDATE messaging_messages SET body='Channel broadcast update'")
    batch = store.next_batch(OWNER, FUTURE)
    excluded = [r for r in batch if r["payload"]["excluded"]]
    assert excluded
    monkeypatch.setattr(
        model, "call_llm_json", lambda *a, **k: pytest.fail("excluded source reached model")
    )
    ctx, fs = model.analyze(OWNER, batch)
    assert fs == []
    store.apply_batch(OWNER, run(database), batch, ctx, fs)
    assert not store.knowledge_for(OWNER)


def test_old_knowledge_cannot_replace_newer_current_facts(database):
    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    fs = store.validate_findings({"findings": [finding(batch[0])]}, batch, context(batch))
    store.apply_batch(OWNER, run(database), batch, context(batch), fs)
    ingest(
        database,
        ident="older",
        body="The earlier quote was still unfinished.",
        sent="2026-01-02T12:00:00+00:00",
    )
    old = store.next_batch(OWNER, FUTURE)
    f = finding(old[0])
    f["statement"] = "The quote is unfinished"
    fs = store.validate_findings({"findings": [f]}, old, context(old))
    store.apply_batch(OWNER, run(database), old, context(old), fs)
    assert store.knowledge_for(OWNER)[0]["statement"] == "Alex received the quote"


def test_uncertain_information_does_not_replace_a_settled_fact_and_expiry_is_visible(database):
    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    f = finding(batch[0])
    f["valid_until"] = "2026-01-04T00:00:00+00:00"
    fs = store.validate_findings({"findings": [f]}, batch, context(batch))
    store.apply_batch(OWNER, run(database), batch, context(batch), fs)
    assert store.knowledge_for(OWNER)[0]["status"] == "historical"
    f.update(status="uncertain", statement="Delivery may be delayed", valid_until=None)
    fs = store.validate_findings({"findings": [f]}, batch, context(batch))
    store.apply_batch(OWNER, run(database), batch, context(batch), fs)
    assert len(store.knowledge_for(OWNER)) == 2


def test_manual_task_changes_are_stamped_and_proposal_changes_require_current_version(database):
    setup_owner(database)
    ingest(database)
    with database() as conn:
        conn.execute(
            "INSERT INTO todos(todo_id,description) VALUES ('task1','Send the quote to Alex')"
        )
    batch = store.next_batch(OWNER, FUTURE)
    ctx = store.context_for(OWNER, batch)
    f = finding(batch[0], kind="task_change")
    f.update(
        existing_id="task1",
        requested_status="cancelled",
        target_updated_at=ctx["tasks"][0]["updated_at"].isoformat(),
    )
    fs = store.validate_findings({"findings": [f]}, batch, ctx)
    store.apply_batch(OWNER, run(database), batch, ctx, fs)
    p = store.list_proposals(OWNER)[0]
    with database() as conn:
        conn.execute(
            "UPDATE todos SET description='A manually revised task', updated_at=NOW() WHERE todo_id='task1'"
        )
        assert (
            conn.execute("SELECT intelligence_manual_at FROM todos").fetchone()[
                "intelligence_manual_at"
            ]
            is not None
        )
    assert store.resolve_proposal(OWNER, p["proposal_id"], accept=True)["reason"] == "task_changed"


def test_project_and_task_change_acceptance_apply_once(database):
    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    ctx = context(batch)
    fs = store.validate_findings({"findings": [finding(batch[0], kind="project")]}, batch, ctx)
    store.apply_batch(OWNER, run(database), batch, ctx, fs)
    p = store.list_proposals(OWNER)[0]
    accepted = store.resolve_proposal(OWNER, p["proposal_id"], accept=True)
    assert accepted["accepted"]
    with database() as conn:
        conn.execute(
            "INSERT INTO todos(todo_id,description) VALUES ('task1','Send the quote to Alex')"
        )
    ctx = store.context_for(OWNER, batch)
    f = finding(batch[0], kind="task_change")
    f.update(
        existing_id="task1",
        project_id=accepted["id"],
        requested_status="cancelled",
        target_updated_at=ctx["tasks"][0]["updated_at"].isoformat(),
    )
    fs = store.validate_findings({"findings": [f]}, batch, ctx)
    store.apply_batch(OWNER, run(database), batch, ctx, fs)
    proposal = store.list_proposals(OWNER)[0]
    assert store.resolve_proposal(OWNER, proposal["proposal_id"], accept=True)["accepted"]
    with database() as conn:
        assert conn.execute("SELECT status FROM todos").fetchone()["status"] == "cancelled"


def test_forwarded_flag_survives_wrappers_without_exposing_forwarding_authors():
    proto = pytest.importorskip("tryx.waproto.whatsapp_pb2")
    from messaging_normalization import Origin, normalize_message

    message = proto.Message()
    nested = message.ephemeralMessage.message.extendedTextMessage
    nested.text = "I sent the quote yesterday."
    nested.contextInfo.isForwarded = True
    normalized = normalize_message(
        proto=message,
        chat="15555550101@s.whatsapp.net",
        sender="15555550100@s.whatsapp.net",
        message_id="sample",
        sent_at=1767441600,
        outgoing=True,
        origin=Origin.LIVE,
    )
    assert normalized["forwarded"] is True
    assert "contextInfo" not in normalized


def test_context_budget_does_not_grow_with_accumulated_evidence():
    huge = {"statement": "sample fact", "evidence": [{"quote": "A" * 10000} for _ in range(1000)]}
    bounded = model._bounded_context({"knowledge": [huge] * 100})
    import json

    assert len(json.dumps(bounded)) < 9000


def test_source_inspection_and_corrections_are_owner_scoped_and_uncached(database, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import intelligence as routes
    from routes import knowledge as knowledge_routes

    monkeypatch.setattr(routes, "get_conn", database)
    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    ctx = context(batch)
    fs = store.validate_findings({"findings": [finding(batch[0])]}, batch, ctx)
    store.apply_batch(OWNER, run(database), batch, ctx, fs)
    knowledge_id = store.knowledge_for(OWNER)[0]["knowledge_id"]
    app = FastAPI()
    app.include_router(routes.create_intelligence_router())
    app.include_router(knowledge_routes.create_knowledge_router())
    # Auth configuration tests reload auth; override the callable captured by this router.
    app.dependency_overrides[routes.get_current_user] = lambda: {"email": OTHER}
    app.dependency_overrides[knowledge_routes.get_current_user] = lambda: {"email": OTHER}
    with TestClient(app) as client:
        assert (
            client.get(f"/mobile/intelligence/sources/{batch[0]['source_id']}").status_code == 404
        )
        assert (
            client.patch(
                f"/mobile/knowledge/{knowledge_id}",
                json={"statement": "A manual correction"},
            ).status_code
            == 404
        )
        app.dependency_overrides[routes.get_current_user] = lambda: {"email": OWNER}
        app.dependency_overrides[knowledge_routes.get_current_user] = lambda: {"email": OWNER}
        response = client.get(f"/mobile/intelligence/sources/{batch[0]['source_id']}")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["speaker"] == "You"
        assert response.json()["source_link"]["url"] == "https://wa.me/15555550101"
        assert (
            client.patch(
                f"/mobile/knowledge/{knowledge_id}",
                json={"statement": "A manual correction"},
            ).status_code
            == 200
        )
    assert store.knowledge_for(OWNER)[0]["manual"] is True


def test_task_detail_exposes_assignment_project_and_owner_checked_evidence(database, monkeypatch):
    import todos

    monkeypatch.setattr(todos, "get_conn", database)
    monkeypatch.setattr(todos, "_collect_todo_links", lambda *a: {})
    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    ctx = context(batch)
    fs = store.validate_findings({"findings": [finding(batch[0], kind="task")]}, batch, ctx)
    store.apply_batch(OWNER, run(database), batch, ctx, fs)
    proposal = store.list_proposals(OWNER)[0]
    result = store.resolve_proposal(OWNER, proposal["proposal_id"], accept=True)
    detail = todos.get_todo(result["id"], user_email=OWNER)
    assert detail["assignee_kind"] == "user"
    assert detail["analysis_evidence"][0]["action"] == "created"
    assert todos.get_todo(result["id"], user_email=OTHER)["analysis_evidence"] == []
    assert todos.normalize_todo_status("cancelled") == "cancelled"


def test_confirmed_completion_withdraws_a_pending_task_suggestion(database):
    setup_owner(database)
    now = store.utcnow()
    ingest(
        database,
        body="I will send Alex the full quote.",
        sent=(now - timedelta(minutes=1)).isoformat(),
    )
    batch = store.next_batch(OWNER, FUTURE)
    ctx = context(batch)
    fs = store.validate_findings({"findings": [finding(batch[0], kind="task")]}, batch, ctx)
    store.apply_batch(OWNER, run(database), batch, ctx, fs)
    p = store.list_proposals(OWNER)[0]
    ingest(database, ident="completion", sent=(now + timedelta(minutes=1)).isoformat())
    batch = store.next_batch(OWNER, FUTURE)
    ctx = store.context_for(OWNER, batch)
    f = finding(batch[0], kind="complete")
    f["existing_id"] = p["proposal_id"]
    fs = store.validate_findings({"findings": [f]}, batch, ctx)
    fs[0]["verified_completion"] = True
    store.apply_batch(OWNER, run(database), batch, ctx, fs)
    assert not store.list_proposals(OWNER)
    assert not store.resolve_proposal(OWNER, p["proposal_id"], accept=True)["accepted"]


def test_late_history_extends_only_the_knowledge_queue(database):
    import knowledge_jobs

    setup_owner(database)
    knowledge_jobs.initialize_owner(OWNER)
    ingest(database, sent="2025-12-30T12:00:00+00:00", ident="historical")
    assert not store.next_batch(OWNER, FUTURE)
    knowledge_jobs.initialize_owner(OWNER)
    historical = knowledge_jobs.claim([OWNER])
    assert historical["historical"]
    ingest(database, ident="fresh")
    current = jobs.claim_run([OWNER])
    assert not current["knowledge_only"]
    batch = store.next_batch(OWNER, current["cutoff"])
    assert len(batch) == 1 and batch[0]["actionable"]
    with database() as conn:
        assert not conn.execute("SELECT 1 FROM intelligence_runs WHERE knowledge_only").fetchone()


def test_self_knowledge_appears_only_on_the_confirmed_owner_contact(database):
    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    ctx = context(batch)
    fs = store.validate_findings({"findings": [finding(batch[0])]}, batch, ctx)
    store.apply_batch(OWNER, run(database), batch, ctx, fs)
    assert not store.knowledge_for(OWNER, contact_id="alex")
    with database() as conn:
        conn.execute("UPDATE contacts SET emails=ARRAY[%s] WHERE contact_id='alex'", (OWNER,))
    assert len(store.knowledge_for(OWNER, contact_id="alex")) == 1


def test_first_observed_edit_without_original_cannot_generate_work(database):
    setup_owner(database)
    ingest(database, kind="edit")
    batch = store.next_batch(OWNER, FUTURE)
    assert batch and not batch[0]["actionable"]
    assert not store.validate_findings(
        {"findings": [finding(batch[0], kind="task")]}, batch, context(batch)
    )
