#!/usr/bin/env python3
"""Repair existing learned prose once, without an LLM or read-time rewriting.

Run inside the updated orchestrator container:
    python scripts/repair_knowledge_prose.py --dry-run
    python scripts/repair_knowledge_prose.py --apply

Optionally restrict either command with --owner reader@example.com.
Dry run is the default. JSON lines show changes followed by totals. Apply keeps
original facts in revision history; affected derived summaries regenerate normally.
Changes commit in bounded batches, and rerunning safely resumes after interruption.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import knowledge_prose
from db import get_conn
from intelligence import json_value


def repair(*, owner=None, apply=False, batch_size=100, emit=None):
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    emit = emit or (lambda _row: None)
    totals = {
        "mode": "apply" if apply else "dry_run",
        "facts_changed": 0,
        "summaries_invalidated": 0,
        "skipped": 0,
    }
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT user_email FROM (
                SELECT user_email FROM intelligence_knowledge UNION
                SELECT user_email FROM knowledge_summaries) owners
            WHERE (%s::text IS NULL OR user_email=%s) ORDER BY user_email""",
            (owner, owner),
        )
        owners = [row["user_email"] for row in cur.fetchall()]
    for current_owner in owners:
        # Invalidate polluted summaries first; fact updates below also use the normal
        # invalidation trigger. Version/token guards discard any stale in-flight output.
        last = None
        while True:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """SELECT * FROM knowledge_summaries WHERE user_email=%s AND summary IS NOT NULL
                    AND (%s::text IS NULL OR subject_ref>%s) ORDER BY subject_ref LIMIT %s"""
                    + (" FOR UPDATE" if apply else ""),
                    (current_owner, last, last, batch_size),
                )
                rows = [dict(row) for row in cur.fetchall()]
                if not rows:
                    break
                last = rows[-1]["subject_ref"]
                changes = [row for row in rows if knowledge_prose.needs_repair(row["summary"])]
                if apply:
                    for row in changes:
                        cur.execute(
                            """UPDATE knowledge_summaries SET summary=NULL,version=version+1,
                            next_attempt_at=NOW(),lease_token=NULL,lease_until=NULL,error_code=NULL
                            WHERE user_email=%s AND subject_ref=%s""",
                            (current_owner, row["subject_ref"]),
                        )
                    conn.commit()
                else:
                    conn.rollback()
                totals["summaries_invalidated"] += len(changes)
                for row in changes:
                    emit(
                        {
                            "kind": "summary_invalidation",
                            "owner": current_owner,
                            "subject_ref": row["subject_ref"],
                        }
                    )
        last = None
        while True:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """SELECT * FROM intelligence_knowledge WHERE user_email=%s
                    AND NOT manual AND deleted_at IS NULL
                    AND (%s::text IS NULL OR knowledge_id>%s)
                    ORDER BY knowledge_id LIMIT %s"""
                    + (" FOR UPDATE" if apply else ""),
                    (current_owner, last, last, batch_size),
                )
                rows = [dict(row) for row in cur.fetchall()]
                if not rows:
                    break
                last = rows[-1]["knowledge_id"]
                originals = [
                    row
                    for row in rows
                    if any(
                        knowledge_prose.needs_repair(row.get(field))
                        for field in ("statement", "inference_reason")
                    )
                ]
                prepared = [dict(row) for row in originals]
                for field in ("statement", "inference_reason"):
                    knowledge_prose.clean_rows(cur, current_owner, prepared, field=field)
                changes = []
                for old, new in zip(originals, prepared, strict=True):
                    before = {field: old[field] for field in ("statement", "inference_reason")}
                    after = {field: new[field] for field in before}
                    if before == after:
                        continue
                    if not 3 <= len(new["statement"]) <= 2000:
                        totals["skipped"] += 1
                        emit(
                            {
                                "kind": "skipped",
                                "knowledge_id": old["knowledge_id"],
                                "reason": "invalid_statement_after_cleanup",
                            }
                        )
                        continue
                    if apply:
                        cur.execute(
                            "INSERT INTO intelligence_knowledge_revisions(knowledge_id,snapshot) VALUES(%s,%s)",
                            (old["knowledge_id"], json_value(old)),
                        )
                        cur.execute(
                            """UPDATE intelligence_knowledge SET statement=%s,inference_reason=%s,
                            embedding=CASE WHEN statement IS DISTINCT FROM %s THEN NULL ELSE embedding END,
                            revision=revision+1,updated_at=NOW() WHERE knowledge_id=%s""",
                            (
                                new["statement"],
                                new["inference_reason"],
                                new["statement"],
                                old["knowledge_id"],
                            ),
                        )
                    changes.append(
                        {
                            "kind": "fact",
                            "owner": current_owner,
                            "knowledge_id": old["knowledge_id"],
                            "before": before,
                            "after": after,
                        }
                    )
                if apply:
                    conn.commit()
                else:
                    conn.rollback()
                totals["facts_changed"] += len(changes)
                for change in changes:
                    emit(change)
    return totals


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Persist the repair")
    mode.add_argument("--dry-run", action="store_true", help="Preview only (default)")
    parser.add_argument("--owner", help="Restrict the repair to one owner's knowledge")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args(argv)
    if not 1 <= args.batch_size <= 1000:
        parser.error("--batch-size must be between 1 and 1000")

    def output(row):
        print(json.dumps(row, ensure_ascii=False, default=str), flush=True)

    totals = repair(owner=args.owner, apply=args.apply, batch_size=args.batch_size, emit=output)
    output(totals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
