# ruff: noqa: F811
"""One-time repair preserves source evidence and history, with no read-time formatting."""

import pytest

import intelligence
import knowledge
from scripts import repair_knowledge_prose as script
from tests.test_intelligence import OTHER, OWNER, database  # noqa: F401
from tests.test_knowledge import save


@pytest.fixture
def seeded(database, monkeypatch):
    monkeypatch.setattr(script, "get_conn", database)
    fact = save(database)
    with database() as conn:
        conn.execute(
            """UPDATE intelligence_knowledge SET statement='contact:alex received the quote.',
            inference_reason='Explicitly confirmed by contact:alex.',embedding=ARRAY[1.0,2.0]
            WHERE knowledge_id=%s""",
            (fact["knowledge_id"],),
        )
        conn.execute(
            """UPDATE knowledge_summaries SET summary='contact:alex received the quote.',
            rendered_version=version,lease_token='old-worker',lease_until=NOW()+INTERVAL '1 hour'
            WHERE user_email=%s""",
            (OWNER,),
        )
        original = dict(conn.execute("SELECT * FROM intelligence_knowledge").fetchone())
        summary = dict(conn.execute("SELECT * FROM knowledge_summaries").fetchone())
    return original, summary


def clone(conn, original, ident, *, owner=OWNER, manual=False, deleted=False):
    conn.execute(
        """INSERT INTO intelligence_knowledge(knowledge_id,user_email,subject_ref,predicate,
        statement,status,evidence,manual,deleted_at) VALUES(%s,%s,'self',%s,%s,'current',%s,%s,
        CASE WHEN %s THEN NOW() ELSE NULL END)""",
        (
            ident,
            owner,
            ident,
            original["statement"],
            intelligence.json_value(original["evidence"]),
            manual,
            deleted,
        ),
    )


def test_default_dry_run_previews_but_never_changes_database(database, seeded):
    original, summary = seeded
    changes = []
    totals = script.repair(emit=changes.append)
    assert totals == {
        "mode": "dry_run",
        "facts_changed": 1,
        "summaries_invalidated": 1,
        "skipped": 0,
    }
    preview = next(row for row in changes if row["kind"] == "fact")
    assert preview["before"]["statement"] == original["statement"]
    assert preview["after"]["statement"] == "Alex Carter received the quote."
    with database() as conn:
        assert dict(conn.execute("SELECT * FROM intelligence_knowledge").fetchone()) == original
        assert dict(conn.execute("SELECT * FROM knowledge_summaries").fetchone()) == summary
        assert (
            conn.execute("SELECT count(*) AS n FROM intelligence_knowledge_revisions").fetchone()[
                "n"
            ]
            == 0
        )
    assert knowledge.list_items(OWNER)[0]["statement"] == original["statement"]


def test_apply_preserves_original_snapshot_and_evidence_then_is_idempotent(database, seeded):
    original, summary = seeded
    totals = script.repair(apply=True)
    assert totals["facts_changed"] == 1 and totals["summaries_invalidated"] == 1
    with database() as conn:
        repaired = conn.execute("SELECT * FROM intelligence_knowledge").fetchone()
        assert repaired["statement"] == "Alex Carter received the quote."
        assert repaired["inference_reason"] == "Explicitly confirmed by Alex Carter."
        assert repaired["evidence"] == original["evidence"]
        assert repaired["subject_ref"] == original["subject_ref"]
        assert repaired["predicate"] == original["predicate"]
        assert repaired["manual"] is False and repaired["inferred"] == original["inferred"]
        assert repaired["embedding"] is None
        assert repaired["revision"] == original["revision"] + 1
        snapshot = conn.execute("SELECT snapshot FROM intelligence_knowledge_revisions").fetchone()[
            "snapshot"
        ]
        assert (
            snapshot["statement"] == original["statement"]
            and snapshot["evidence"] == original["evidence"]
        )
        current_summary = conn.execute("SELECT * FROM knowledge_summaries").fetchone()
        assert (
            current_summary["summary"] is None and current_summary["version"] > summary["version"]
        )
        assert current_summary["lease_token"] is None and current_summary["lease_until"] is None
    assert knowledge.list_items(OWNER)[0]["statement"] == "Alex Carter received the quote."
    assert script.repair(apply=True)["facts_changed"] == 0
    with database() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM intelligence_knowledge_revisions").fetchone()[
                "n"
            ]
            == 1
        )


def test_owner_scope_manual_and_deleted_facts_are_preserved(database, seeded):
    original, _ = seeded
    with database() as conn:
        clone(conn, original, "manual", manual=True)
        clone(conn, original, "deleted", deleted=True)
        clone(conn, original, "other", owner=OTHER)
    assert script.repair(owner=OWNER, apply=True, batch_size=1)["facts_changed"] == 1
    with database() as conn:
        for ident in ("manual", "deleted", "other"):
            assert (
                conn.execute(
                    "SELECT statement FROM intelligence_knowledge WHERE knowledge_id=%s", (ident,)
                ).fetchone()["statement"]
                == original["statement"]
            )
    assert script.repair(owner=OTHER, apply=True)["facts_changed"] == 1


def test_batch_failure_rolls_back_all_fact_updates_and_revision_inserts(
    database, seeded, monkeypatch
):
    original, _ = seeded
    with database() as conn:
        clone(conn, original, "second-fact")
    real_json_value = script.json_value
    calls = []

    def fail_second(value):
        calls.append(value)
        if len(calls) == 2:
            raise RuntimeError("synthetic interrupted batch")
        return real_json_value(value)

    monkeypatch.setattr(script, "json_value", fail_second)
    with pytest.raises(RuntimeError, match="synthetic interrupted batch"):
        script.repair(apply=True)
    with database() as conn:
        assert all(
            row["statement"] == original["statement"]
            for row in conn.execute("SELECT statement FROM intelligence_knowledge")
        )
        assert (
            conn.execute("SELECT count(*) AS n FROM intelligence_knowledge_revisions").fetchone()[
                "n"
            ]
            == 0
        )
    monkeypatch.setattr(script, "json_value", real_json_value)
    assert script.repair(apply=True, batch_size=1)["facts_changed"] == 2


def test_summary_only_pollution_invalidates_without_touching_clean_fact(database, seeded):
    with database() as conn:
        conn.execute(
            "UPDATE intelligence_knowledge SET statement='Alex received the quote.',inference_reason=NULL"
        )
        conn.execute("UPDATE knowledge_summaries SET summary='contact:alex received the quote.'")
    totals = script.repair(apply=True)
    assert totals["facts_changed"] == 0 and totals["summaries_invalidated"] == 1
    with database() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM intelligence_knowledge_revisions").fetchone()[
                "n"
            ]
            == 0
        )
        assert conn.execute("SELECT summary FROM knowledge_summaries").fetchone()["summary"] is None


def test_cli_defaults_to_preview_and_apply_is_explicit(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(script, "repair", lambda **kwargs: calls.append(kwargs) or {"mode": "test"})
    assert script.main([]) == 0 and calls[-1]["apply"] is False
    assert script.main(["--apply", "--owner", OWNER]) == 0
    assert calls[-1]["apply"] is True and calls[-1]["owner"] == OWNER
    assert '"mode": "test"' in capsys.readouterr().out
