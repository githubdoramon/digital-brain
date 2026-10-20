# ruff: noqa: F811
"""No-op saves are not new evidence; real note changes retain their chronology."""

from pathlib import Path

import pytest

import intelligence
import knowledge
import knowledge_jobs
import learning_progress
from tests.test_intelligence import OTHER, OWNER, database, setup_owner  # noqa: F401

MIGRATION = (
    Path(__file__).resolve().parents[1] / "db_migrations/0085_learning_fairness_note_dedup.sql"
)


def edit(database, text):
    with database() as conn:
        conn.execute("UPDATE contacts SET comments=%s WHERE contact_id='alex'", (text,))


def notes(database):
    with database() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM intelligence_sources WHERE kind='contact_note' ORDER BY source_id"
            )
        ]


def test_noop_saves_do_not_capture_or_queue_new_note_evidence(database):
    edit(database, "Alex Carter prefers quiet keyboards.")
    knowledge_jobs.initialize_owner(OWNER)
    original = notes(database)[0]
    for _ in range(4):
        edit(database, "Alex Carter prefers quiet keyboards.")
    with database() as conn:
        conn.execute(
            "UPDATE contacts SET tags=ARRAY['friend'],comments=comments WHERE contact_id='alex'"
        )
    knowledge_jobs.initialize_owner(OWNER)
    assert len(notes(database)) == 1
    assert notes(database)[0]["payload"] == original["payload"]
    assert sum(knowledge_jobs.progress(OWNER).values()) == 1


def test_real_edits_reversions_and_contact_renames_keep_distinct_sources(database):
    for text in (
        "Prefers quiet keyboards.",
        "Prefers tactile keyboards.",
        "Prefers quiet keyboards.",
    ):
        edit(database, text)
    with database() as conn:
        conn.execute("UPDATE contacts SET display_name='Alex Lane' WHERE contact_id='alex'")
    rows = notes(database)
    assert len(rows) == 4
    assert rows[0]["payload"]["text"] == rows[2]["payload"]["text"]
    assert all(row["duplicate_of_source_id"] is None for row in rows)
    assert rows[3]["payload"]["title"] == "Alex Lane"


def test_noop_save_of_a_seeded_note_keeps_unknown_source_date(database):
    edit(database, "Alex Carter prefers quiet keyboards.")
    with database() as conn:
        conn.execute("DELETE FROM intelligence_sources WHERE kind='contact_note'")
        conn.execute(
            """INSERT INTO intelligence_sources(kind,entity_id,fingerprint,payload,historical)
            VALUES('contact_note','alex','legacy',%s,TRUE)""",
            (
                intelligence.json_value(
                    {
                        "text": "Alex Carter prefers quiet keyboards.",
                        "title": "Alex Carter",
                        "contact_id": "alex",
                        "sent_at": None,
                    }
                ),
            ),
        )
    edit(database, "Alex Carter prefers quiet keyboards.")
    assert len(notes(database)) == 1 and notes(database)[0]["payload"]["sent_at"] is None
    edit(database, "Alex Carter prefers tactile keyboards.")
    assert len(notes(database)) == 2


@pytest.mark.parametrize("database", ["pre_fairness"], indirect=True)
def test_migration_excludes_only_consecutive_noops_and_preserves_receipts_and_evidence(database):
    setup_owner(database)
    for text in (
        "Prefers quiet keyboards.",
        "Prefers quiet keyboards.",
        "Prefers tactile keyboards.",
        "Prefers tactile keyboards.",
        "Prefers quiet keyboards.",
        "Prefers quiet keyboards.",
    ):
        edit(database, text)
    original = notes(database)
    assert len(original) == 6
    ids = [row["source_id"] for row in original]
    evidence = [
        {
            "source_id": ids[1],
            "kind": "contact_note",
            "entity_id": "alex",
            "part": 0,
            "quote": "Prefers quiet keyboards.",
            "sent_at": original[1]["payload"]["sent_at"],
        }
    ]
    with database() as conn:
        for i, ident in enumerate(ids):
            status = {1: "done", 3: "deferred", 5: "running"}.get(i, "pending")
            for owner in (OWNER, OTHER):
                conn.execute(
                    """INSERT INTO knowledge_learning(user_email,source_id,status,attempts,lease_token,lease_until)
                    VALUES(%s,%s,%s,1,'original-token',NOW()+INTERVAL '15 minutes')""",
                    (owner, ident, status),
                )
        conn.execute(
            """INSERT INTO intelligence_knowledge(knowledge_id,user_email,subject_ref,predicate,statement,status,context,evidence,manual)
            VALUES('protected',%s,'self','keyboard','The corrected keyboard preference.','current','personal',%s,TRUE)""",
            (OWNER, intelligence.json_value(evidence)),
        )
        before = [
            dict(r)
            for r in conn.execute("SELECT * FROM knowledge_learning ORDER BY user_email,source_id")
        ]
        conn.execute(MIGRATION.read_text())
        after = [
            dict(r)
            for r in conn.execute("SELECT * FROM knowledge_learning ORDER BY user_email,source_id")
        ]
        assert before == after
        fact = conn.execute(
            "SELECT manual,statement,evidence FROM intelligence_knowledge WHERE knowledge_id='protected'"
        ).fetchone()
        assert fact["manual"] and fact["statement"] == "The corrected keyboard preference."
        assert fact["evidence"] == evidence
    migrated = notes(database)
    assert [r["duplicate_of_source_id"] for r in migrated] == [
        None,
        ids[0],
        None,
        ids[2],
        None,
        ids[4],
    ]
    assert [r["payload"] for r in migrated] == [r["payload"] for r in original]
    assert [r["fingerprint"] for r in migrated] == [r["fingerprint"] for r in original]
    knowledge_jobs.initialize_owner(OWNER)
    report = learning_progress.snapshot(OWNER)
    assert report["deduplication"]["ignored_note_versions"] == 3
    assert sum(report["counts"].values()) == 3
    assert report["learning"]["history"]["remaining"] == 3
    for ident in ids[::2]:
        job = knowledge_jobs.claim([OWNER])
        assert job["source_id"] == ident
        with database() as conn:
            conn.execute(
                "UPDATE knowledge_learning SET status='done',lease_until=NULL WHERE user_email=%s AND source_id=%s",
                (OWNER, ident),
            )
    assert knowledge_jobs.claim([OWNER]) is None
    # Duplicate snapshots remain valid provenance for already-saved facts.
    with database() as conn:
        assert knowledge.source_is_current(conn.cursor(), migrated[1], OWNER)


@pytest.mark.parametrize("database", ["pre_fairness"], indirect=True)
def test_reference_recovery_does_not_reset_other_failures_or_ambiguities(database):
    setup_owner(database)
    for index in range(4):
        edit(database, f"Keyboard preference revision {index}.")
    with database() as conn:
        for row, status, code in zip(
            notes(database),
            ("failed", "failed", "deferred", "running"),
            ("invalid_subject", "invalid_evidence", None, None),
            strict=True,
        ):
            conn.execute(
                """INSERT INTO knowledge_learning(user_email,source_id,status,attempts,error_code,lease_token,lease_until)
                VALUES(%s,%s,%s,3,%s,'unchanged',NOW()+INTERVAL '15 minutes')""",
                (OWNER, row["source_id"], status, code),
            )
        conn.execute(MIGRATION.read_text())
        rows = conn.execute(
            "SELECT status,attempts,error_code,lease_token FROM knowledge_learning ORDER BY source_id"
        ).fetchall()
    assert rows[0]["status"] == "pending" and rows[0]["attempts"] == 0
    assert rows[1]["status"] == "failed" and rows[1]["error_code"] == "invalid_evidence"
    assert rows[2]["status"] == "deferred" and rows[3]["status"] == "running"
    assert rows[3]["lease_token"] == "unchanged"
