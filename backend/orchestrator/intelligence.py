"""Owner-scoped, evidence-backed knowledge and proposed work.

The model supplies candidates. This module owns validation and atomic effects.
Source snapshots and chunk receipts make interrupted runs replayable without
using a high-water sequence that could skip a concurrently committed record.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

import knowledge_prose
from db import get_conn
from job_leases import LEASE_INTERVAL_SQL
from knowledge_references import contact_ref, normalize_finding
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

CHUNK_SIZE = 5000
BATCH_SIZE = 12
MAX_FINDINGS = 20


def utcnow():
    return datetime.now(timezone.utc)


def key(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def json_value(value):
    return Jsonb(json.loads(json.dumps(value, default=str)))


def initialize(owner: str):
    """Register the activation boundary once; seed existing sources as knowledge only."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO intelligence_state(user_email) VALUES (%s) ON CONFLICT DO NOTHING",
            (owner,),
        )
        cur.execute("SELECT * FROM intelligence_state WHERE user_email = %s FOR UPDATE", (owner,))
        state = dict(cur.fetchone())
        if not state["seeded"]:
            cur.execute("SELECT pg_advisory_xact_lock(732141974)")
            for kind, table, ident, function, owner_sql, where, params in (
                (
                    "message",
                    "messaging_messages",
                    "message_id",
                    "intelligence_message_payload",
                    "a.user_email",
                    "JOIN messaging_accounts a USING(account_id) WHERE a.user_email = %s",
                    (owner,),
                ),
                ("event", "events", "id", "intelligence_event_payload", "NULL", "WHERE TRUE", ()),
                (
                    "document",
                    "documents",
                    "document_id",
                    "intelligence_document_payload",
                    "NULL",
                    "WHERE TRUE",
                    (),
                ),
            ):
                # SQL identifiers are static application constants, never model/user input.
                cur.execute(
                    f"""
                    INSERT INTO intelligence_sources(kind, entity_id, owner_email, fingerprint, payload, historical)
                    SELECT %s, s.{ident}, {owner_sql}, md5({function}(s)::text), {function}(s), TRUE
                    FROM {table} s {where} AND NOT EXISTS (
                        SELECT 1 FROM intelligence_sources x WHERE x.kind = %s AND x.entity_id = s.{ident})
                """,
                    (kind, *params, kind),
                )
            cur.execute(
                "UPDATE intelligence_state SET seeded = TRUE WHERE user_email = %s", (owner,)
            )
        conn.commit()
    return state


def next_batch(owner: str, cutoff: datetime, *, knowledge_only=False, review=False):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.*, p.part, st.started_at,
                substring(s.payload->>'text' FROM p.part * %s + 1 FOR %s) AS text
            FROM intelligence_sources s CROSS JOIN intelligence_state st
            CROSS JOIN LATERAL generate_series(0,
                GREATEST(0, (length(COALESCE(s.payload->>'text', '')) - 1) / %s)) p(part)
            WHERE s.kind IN ('message','event','document') AND st.user_email = %s AND (s.owner_email IS NULL OR s.owner_email = %s)
              AND s.observed_at <= %s
              AND (%s = (s.historical OR s.observed_at < st.started_at
                   OR COALESCE((s.payload->>'sent_at')::timestamptz < st.started_at, FALSE)))
              AND (%s = FALSE OR (s.observed_at >= %s::timestamptz - INTERVAL '2 days'
                  AND NOT s.historical AND p.part = 0))
              AND (%s OR NOT EXISTS (SELECT 1 FROM intelligence_processed done
                  WHERE done.user_email = %s AND done.source_id = s.source_id AND done.part = p.part))
              AND NOT EXISTS (SELECT 1 FROM intelligence_failures failed
                  WHERE failed.user_email = st.user_email AND failed.source_id = s.source_id
                    AND failed.part = p.part AND failed.review = %s
                    AND (failed.next_attempt_at IS NULL OR failed.next_attempt_at > NOW()))
            ORDER BY s.historical, s.source_id, p.part LIMIT %s
        """,
            (
                CHUNK_SIZE,
                CHUNK_SIZE + 400,
                CHUNK_SIZE,
                owner,
                owner,
                cutoff,
                knowledge_only,
                review,
                cutoff,
                review,
                owner,
                review,
                BATCH_SIZE,
            ),
        )
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["ref"] = f"{r['source_id']}:{r['part']}"
            r["actionable"] = (
                not knowledge_only and not r["historical"] and r["observed_at"] >= r["started_at"]
            )
            if r["payload"].get("sent_at"):
                sent = datetime.fromisoformat(r["payload"]["sent_at"])
                r["actionable"] = r["actionable"] and sent >= r["started_at"]
            if r["kind"] == "message":
                r["actionable"] = r["actionable"] and r["payload"].get("original_known", False)
            r["actionable"] = r["actionable"] and not r["payload"].get("deleted", False)
            r["skip"] = bool(
                r["payload"].get("deleted")
                or r["payload"].get("excluded")
                or not _current(cur, r, owner)
            )
    return rows


def progress(owner, cutoff, *, knowledge_only=False):
    """Snapshot source-part coverage, including deferred work, without exposing evidence."""
    with get_conn() as conn, conn.cursor() as cur:
        return progress_with_cursor(cur, owner, cutoff, knowledge_only=knowledge_only)


def progress_with_cursor(cur, owner, cutoff, *, knowledge_only=False):
    """Use the caller's snapshot for API and periodic worker reports."""
    cur.execute(
        """SELECT COUNT(*) AS total_parts,
            COUNT(*) FILTER (WHERE done.source_id IS NOT NULL) AS processed_parts,
            COUNT(*) FILTER (WHERE done.source_id IS NULL) AS remaining_parts,
            COUNT(*) FILTER (WHERE done.source_id IS NULL AND
                (f.source_id IS NULL OR f.next_attempt_at <= NOW())) AS ready_parts,
            COUNT(*) FILTER (WHERE done.source_id IS NULL AND
                f.next_attempt_at > NOW()) AS retrying_parts,
            COUNT(*) FILTER (WHERE done.source_id IS NULL AND
                f.source_id IS NOT NULL AND f.next_attempt_at IS NULL) AS paused_parts,
            MIN(s.observed_at) FILTER (WHERE done.source_id IS NULL) AS oldest_pending_observed_at,
            MIN((s.payload->>'sent_at')::timestamptz) FILTER (WHERE done.source_id IS NULL) AS oldest_pending_source_at,
            MIN(f.next_attempt_at) FILTER (WHERE done.source_id IS NULL AND f.next_attempt_at>NOW()) AS next_retry_at,
            MAX(done.processed_at) AS last_success_at
            FROM intelligence_sources s JOIN intelligence_state st ON st.user_email=%s
            CROSS JOIN LATERAL generate_series(0, GREATEST(0,
                (length(COALESCE(s.payload->>'text','')) - 1) / %s)) p(part)
            LEFT JOIN intelligence_processed done ON done.user_email=st.user_email
                AND done.source_id=s.source_id AND done.part=p.part
            LEFT JOIN intelligence_failures f ON f.user_email=st.user_email
                AND f.source_id=s.source_id AND f.part=p.part AND NOT f.review
            WHERE s.kind IN ('message','event','document')
                AND (s.owner_email IS NULL OR s.owner_email=st.user_email)
                AND s.observed_at <= %s
                AND (%s = (s.historical OR s.observed_at < st.started_at
                    OR COALESCE((s.payload->>'sent_at')::timestamptz < st.started_at, FALSE)))""",
        (owner, CHUNK_SIZE, cutoff, knowledge_only),
    )
    counts = dict(cur.fetchone())
    cur.execute(
        """SELECT COUNT(*) FILTER (WHERE f.next_attempt_at IS NOT NULL) AS review_retrying,
            COUNT(*) FILTER (WHERE f.next_attempt_at IS NULL) AS review_paused
            FROM intelligence_failures f JOIN intelligence_sources s USING(source_id)
            WHERE f.user_email=%s AND f.knowledge_only=%s AND f.review AND s.observed_at <= %s""",
        (owner, knowledge_only, cutoff),
    )
    return {**counts, **dict(cur.fetchone())}


def _query(text):
    words = list(dict.fromkeys(re.findall(r"[^\W_]{3,}", text.lower(), re.UNICODE)))
    return " | ".join(words[:40]) or "intelligenceemptyquery"


def context_for(owner, batch, extra_text=""):
    """Bound related retrieval, including dismissed proposals and completed tasks."""
    query = _query(extra_text or " ".join(r["text"] for r in batch))
    identities = list({r["payload"].get("identity_id") for r in batch} - {None})
    conversations = list({r["payload"].get("conversation_id") for r in batch} - {None})
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT i.identity_id, i.contact_id, i.display_name, i.is_self
            FROM messaging_identities i JOIN messaging_accounts a USING(account_id)
            WHERE a.user_email = %s AND (i.identity_id = ANY(%s) OR i.identity_id IN (
                SELECT sender_identity_id FROM messaging_messages WHERE conversation_id = ANY(%s)))
            ORDER BY (i.identity_id = ANY(%s)) DESC LIMIT 150""",
            (owner, identities, conversations, identities),
        )
        people = [dict(r) for r in cur.fetchall()]
        contact_ids = list(
            (
                {r["contact_id"] for r in people}
                | {r["payload"].get("contact_id") for r in batch}
                | {ident for r in batch for ident in r["payload"].get("confirmed_contact_ids", [])}
            )
            - {None}
        )
        # The installation's events/documents/tasks/contacts have shared access,
        # just as their existing authenticated APIs do. Private derivations do not.
        cur.execute(
            """SELECT contact_id, display_name, aliases, birthday, birthday_month, birthday_day, tags, comments FROM contacts
            WHERE contact_id = ANY(%s) OR to_tsvector('simple', display_name) @@ to_tsquery('simple', %s)
            LIMIT 40""",
            (contact_ids, query),
        )
        contacts = [dict(r) for r in cur.fetchall()]
        cur.execute(
            """SELECT relationship_id,from_contact_id,to_contact_id,relationship_type,reciprocal_type
            FROM contact_relationships WHERE from_contact_id=ANY(%s) OR to_contact_id=ANY(%s)
            ORDER BY relationship_id LIMIT 100""",
            ([c["contact_id"] for c in contacts], [c["contact_id"] for c in contacts]),
        )
        contact_relationships = [dict(r) for r in cur.fetchall()]
        related_contact_ids = list(
            {r[k] for r in contact_relationships for k in ("from_contact_id", "to_contact_id")}
        )
        if related_contact_ids:
            cur.execute(
                """SELECT contact_id,display_name,aliases,birthday,birthday_month,birthday_day,tags,comments
                FROM contacts WHERE contact_id=ANY(%s) ORDER BY contact_id LIMIT 100""",
                (related_contact_ids,),
            )
            contacts = list(
                {c["contact_id"]: c for c in contacts + [dict(r) for r in cur.fetchall()]}.values()
            )
        cur.execute(
            """SELECT k.* FROM intelligence_knowledge k
            WHERE user_email = %s AND (subject_ref = ANY(%s)
                OR to_tsvector('simple', statement) @@ to_tsquery('simple', %s))
            ORDER BY updated_at DESC LIMIT 60""",
            (
                owner,
                ["identity:" + i for i in identities]
                + [contact_ref(c) for c in contact_ids]
                + ["self"],
                query,
            ),
        )
        knowledge = [dict(r) for r in cur.fetchall()]
        cur.execute(
            """SELECT t.* FROM todos t WHERE (analysis_owner IS NULL OR analysis_owner = %s)
            AND (to_tsvector('simple', description) @@ to_tsquery('simple', %s)
                OR (status='pending' AND due_date <= CURRENT_DATE + 14)
                OR todo_id IN (SELECT todo_id FROM todo_contacts WHERE contact_id = ANY(%s)))
            ORDER BY updated_at DESC LIMIT 80""",
            (owner, query, contact_ids),
        )
        tasks = [dict(r) for r in cur.fetchall()]
        cur.execute(
            """SELECT * FROM intelligence_proposals WHERE user_email = %s
            AND to_tsvector('simple', payload::text) @@ to_tsquery('simple', %s)
            ORDER BY created_at DESC LIMIT 60""",
            (owner, query),
        )
        proposals = [dict(r) for r in cur.fetchall()]
        for p in proposals:
            if p["kind"] == "task" and p["status"] == "pending":
                tasks.append(
                    {
                        "todo_id": p["proposal_id"],
                        "description": p["payload"]["statement"],
                        "status": "pending",
                        "created_at": p["created_at"],
                        "updated_at": p["created_at"],
                        "assignee_kind": p["payload"]["assignee_kind"],
                        "assignee_ref": p["payload"].get("assignee_ref"),
                        "intelligence_manual_at": None,
                        "is_proposal": True,
                    }
                )
        cur.execute(
            """SELECT * FROM intelligence_projects WHERE user_email = %s
            AND to_tsvector('simple', title || ' ' || description) @@ to_tsquery('simple', %s)
            ORDER BY created_at DESC LIMIT 30""",
            (owner, query),
        )
        projects = [dict(r) for r in cur.fetchall()]
        cur.execute(
            """SELECT m.message_id, m.body, m.sent_at, m.sender_identity_id, m.outgoing,
                m.conversation_id FROM messaging_messages m JOIN messaging_accounts a USING(account_id)
            WHERE a.user_email = %s AND NOT m.deleted AND EXISTS (
                SELECT 1 FROM jsonb_to_recordset(%s) AS anchor(conversation_id TEXT, sent_at TIMESTAMPTZ)
                WHERE anchor.conversation_id=m.conversation_id
                    AND m.sent_at >= anchor.sent_at - INTERVAL '2 days' AND m.sent_at <= anchor.sent_at)
            ORDER BY m.sent_at DESC LIMIT 60""",
            (
                owner,
                Jsonb(
                    [
                        {
                            "conversation_id": r["payload"]["conversation_id"],
                            "sent_at": r["payload"]["sent_at"],
                        }
                        for r in batch
                        if r["kind"] == "message"
                    ]
                ),
            ),
        )
        lookback = [{**dict(r), "body": r["body"][:1500]} for r in cur.fetchall()]
        cur.execute(
            """SELECT 'event' AS kind, id AS id, title, left(summary, 2000) AS text FROM events
            WHERE NOT is_cancelled AND to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(summary,''))
                @@ to_tsquery('simple', %s) LIMIT 8""",
            (query,),
        )
        related = [dict(r) for r in cur.fetchall()]
        cur.execute(
            """SELECT 'document' AS kind, document_id AS id, title, left(content, 2000) AS text FROM documents
            WHERE to_tsvector('simple', title || ' ' || coalesce(content,''))
                @@ to_tsquery('simple', %s) LIMIT 8""",
            (query,),
        )
        related += [dict(r) for r in cur.fetchall()]
    import knowledge as shared_knowledge

    entities = shared_knowledge.entities_for(
        owner, query=extra_text or " ".join(r["text"] for r in batch), limit=40
    )
    entities += [
        {
            "entity_ref": r["kind"] + ":" + r["id"],
            "kind": r["kind"],
            "label": r.get("title") or r["kind"].title(),
        }
        for r in related
    ]
    entities += [
        {"entity_ref": "project:" + p["project_id"], "kind": "project", "label": p["title"]}
        for p in projects
    ]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT place_id,name FROM places WHERE to_tsvector('simple',COALESCE(name,'')) @@ to_tsquery('simple',%s) LIMIT 20",
            (query,),
        )
        entities += [
            {"entity_ref": "place:" + p["place_id"], "kind": "place", "label": p["name"]}
            for p in cur.fetchall()
        ]

    return {
        "entities": entities,
        "source_context": batch,
        "people": people,
        "contacts": contacts,
        "contact_relationships": contact_relationships,
        "knowledge": knowledge,
        "tasks": tasks,
        "proposals": proposals,
        "projects": projects,
        "lookback": lookback,
        "related": related,
    }


def _current(cur, row, owner):
    """Reject stale evidence and source deletions/monitoring changes during inference."""
    import knowledge

    current = knowledge.source_is_current(cur, row, owner)
    if current is not None:
        return current
    if row["kind"] == "message":
        cur.execute(
            """SELECT md5(intelligence_message_payload(m)::text) AS fingerprint,
                intelligence_message_payload(m) AS payload,
                c.analysis_excluded OR c.kind = 'channel' AS excluded
            FROM messaging_messages m JOIN messaging_accounts a USING(account_id)
            JOIN messaging_conversations c USING(conversation_id, account_id)
            WHERE m.message_id = %s AND a.user_email = %s FOR SHARE OF m, c""",
            (row["entity_id"], owner),
        )
    else:
        table, ident, function = {
            "event": ("events", "id", "intelligence_event_payload"),
            "document": ("documents", "document_id", "intelligence_document_payload"),
        }[row["kind"]]
        cur.execute(
            f"SELECT md5({function}(s)::text) AS fingerprint, FALSE AS excluded FROM {table} s WHERE {ident} = %s FOR SHARE",
            (row["entity_id"],),
        )
    current = cur.fetchone()
    if current and row["kind"] == "message":
        semantic_fields = {
            "text",
            "sent_at",
            "effective_at",
            "deleted",
            "outgoing",
            "forwarded",
            "identity_id",
            "conversation_id",
            "attachments",
        }
        return not current["excluded"] and all(
            current["payload"].get(k) == row["payload"].get(k) for k in semantic_fields
        )
    return bool(
        current and current["fingerprint"] == row["fingerprint"] and not current["excluded"]
    )


def evidence_for(finding, batch):
    from learning_validation import reject

    refs = {r["ref"]: r for r in batch}
    evidence = finding.get("evidence")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 8:
        reject("invalid_evidence", "invalid_evidence_count", finding=finding)
    result = []
    for e in evidence:
        row = refs.get(e.get("ref"))
        quote = e.get("quote", "")
        if (
            not row
            or not isinstance(quote, str)
            or not 8 <= len(quote.strip()) <= 1200
            or quote not in row["text"]
        ):
            reject(
                "invalid_evidence",
                "unknown_source_or_non_exact_quote",
                finding=finding,
                citation=e,
                allowed_refs=list(refs),
                source_text=row["text"] if row else None,
            )
        if row["payload"].get("deleted") or row["payload"].get("excluded"):
            reject("invalid_evidence", "excluded_source", citation=e, finding=finding)
        result.append(
            {
                "source_id": row["source_id"],
                "part": row["part"],
                "quote": quote,
                "kind": row["kind"],
                "entity_id": row["entity_id"],
                "sent_at": row["payload"].get("sent_at"),
                "effective_at": row["payload"].get("effective_at"),
            }
        )
    return result


def validate_findings(result, batch, context, *, knowledge_only=False):
    findings = result.get("findings")
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS:
        raise ValueError("invalid_analysis")
    subjects = {"self", "general"}
    subjects.update("identity:" + p["identity_id"] for p in context["people"])
    subjects.update(contact_ref(p["contact_id"]) for p in context["contacts"])
    known_tasks = {t["todo_id"]: t for t in context["tasks"]}
    known_knowledge = {k["knowledge_id"]: k for k in context["knowledge"]}
    projects = {p["project_id"] for p in context["projects"]}
    refs = {r["source_id"]: r for r in batch}
    validated = []
    for f in findings:
        f = normalize_finding(f, context)
        if f.get("kind") not in {
            "knowledge",
            "task",
            "project",
            "task_change",
            "complete",
            "contact_change",
        }:
            raise ValueError("invalid_kind")
        e = evidence_for(f, batch)
        if f["kind"] not in {"knowledge", "contact_change"} and (
            knowledge_only or not any(refs[x["source_id"]]["actionable"] for x in e)
        ):
            # Old imports are valid knowledge evidence but never actionable work.
            continue
        if f.get("subject_ref") not in subjects and f["kind"] != "knowledge":
            from learning_validation import reject

            reject(
                "invalid_subject", "unknown_subject_ref", finding=f, allowed_refs=sorted(subjects)
            )
        if not isinstance(f.get("statement"), str) or not 3 <= len(f["statement"]) <= 2000:
            raise ValueError("invalid_statement")
        if f["kind"] == "contact_change":
            import contact_learning

            validated.append({**contact_learning.validate(f, context, e), "evidence": e})
            continue
        if f.get("project_id") and f["project_id"] not in projects:
            raise ValueError("invalid_project")
        if f.get("existing_id"):
            existing = known_knowledge if f["kind"] == "knowledge" else known_tasks
            if f["existing_id"] not in existing:
                raise ValueError("invalid_target")
            if f["kind"] == "task":
                continue
        if f["kind"] in {"complete", "task_change"} and f.get("existing_id") not in known_tasks:
            raise ValueError("invalid_target")
        if f["kind"] == "task_change" and known_tasks[f["existing_id"]].get("is_proposal"):
            continue
        if f["kind"] == "knowledge":
            import knowledge

            if knowledge.ambiguous_contact(f, context, e):
                context.setdefault("deferred_refs", []).extend(x["ref"] for x in f["evidence"])
                continue
            f = {**f, **knowledge.validate_entities(f, context, e)}
            labels = knowledge_prose.context_labels(context)
            labels.update({entity["entity_ref"]: entity["label"] for entity in f["entity_records"]})
            f["statement"] = knowledge_prose.clean(f["statement"], labels)
            if not 3 <= len(f["statement"]) <= 2000:
                raise ValueError("invalid_statement")
            if f.get("inference_reason"):
                f["inference_reason"] = knowledge_prose.clean(f["inference_reason"], labels)
            if f.get("inferred") and not f.get("inference_reason"):
                raise ValueError("invalid_analysis")
            if f.get("category", "general") not in knowledge.CATEGORIES:
                raise ValueError("invalid_analysis")
            if f.get("status") not in {"current", "historical", "uncertain", "conflicted"}:
                raise ValueError("invalid_knowledge_status")
            if not isinstance(f.get("predicate"), str) or not re.fullmatch(
                r"[a-z0-9_.-]{1,100}", f["predicate"]
            ):
                raise ValueError("invalid_predicate")
            for boundary in ("valid_from", "valid_until"):
                if f.get(boundary) and datetime.fromisoformat(f[boundary]).tzinfo is None:
                    raise ValueError("missing_applicability_timezone")
        if f.get("assignee_kind") not in {"user", "contact", "identity", "unknown"}:
            raise ValueError("invalid_assignee")
        if (
            f["assignee_kind"] in {"contact", "identity"}
            and f"{f['assignee_kind']}:{f.get('assignee_ref')}" not in subjects
        ):
            raise ValueError("invalid_assignee")
        if f.get("due_date"):
            from datetime import date

            date.fromisoformat(f["due_date"])
        validated.append({**f, "evidence": e})
    return validated


def apply_batch(owner, run, batch, context, findings, *, review=False):
    """Commit findings and receipts together, guarded by the claimed run lease."""
    counts = dict.fromkeys(
        (
            "parts_processed",
            "facts_created",
            "facts_updated",
            "facts_supported",
            "facts_protected",
            "proposals_created",
            "tasks_completed",
            "proposals_expired",
            "findings_skipped",
            "contact_changes_applied",
            "contact_changes_supported",
        ),
        0,
    )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM intelligence_runs WHERE run_id = %s AND user_email = %s
            AND lease_token = %s AND lease_until > NOW() FOR UPDATE""",
            (run["run_id"], owner, run["lease_token"]),
        )
        if not cur.fetchone():
            raise ValueError("lease_expired")
        cur.execute("SELECT set_config('brain.intelligence_write', 'on', TRUE)")
        current = {r["source_id"] for r in batch if _current(cur, r, owner)}
        tasks = {t["todo_id"]: t for t in context.get("tasks", [])}
        for f in findings:
            if any(e["source_id"] not in current for e in f["evidence"]):
                counts["findings_skipped"] += 1
                continue
            if f["kind"] == "knowledge":
                outcome = _save_knowledge(cur, owner, f)
            elif f["kind"] == "contact_change":
                import contact_learning

                outcome = contact_learning.save(cur, owner, f)
            elif f["kind"] == "complete":
                outcome = _complete_task(cur, owner, f, tasks[f["existing_id"]], batch, context)
            else:
                outcome = _save_proposal(cur, owner, f)
            counts[outcome or "findings_skipped"] += 1
        for ref in context.get("deferred_refs", []):
            row = next((r for r in batch if r["ref"] == ref), None)
            if row:
                cur.execute(
                    """INSERT INTO knowledge_learning(user_email,source_id,part,status,historical)
                    VALUES(%s,%s,%s,%s,TRUE) ON CONFLICT(user_email,source_id,part)
                    DO UPDATE SET status=EXCLUDED.status,next_attempt_at=NOW(),
                        attempts=CASE WHEN EXCLUDED.status='pending' THEN 0 ELSE knowledge_learning.attempts END""",
                    (
                        owner,
                        row["source_id"],
                        row["part"],
                        "pending" if counts["contact_changes_applied"] else "deferred",
                    ),
                )
        for r in batch:
            cur.execute(
                """DELETE FROM intelligence_failures WHERE user_email=%s AND source_id=%s
                AND part=%s AND review=%s""",
                (owner, r["source_id"], r["part"], review),
            )
            cur.execute(
                """INSERT INTO intelligence_processed(user_email, source_id, part)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                (owner, r["source_id"], r["part"]),
            )
            counts["parts_processed"] += cur.rowcount
        cur.execute(
            f"""UPDATE intelligence_runs SET batches = batches + 1,
            lease_until = NOW() + {LEASE_INTERVAL_SQL}, reviewed=reviewed OR %s WHERE run_id = %s""",
            (review, run["run_id"]),
        )
        conn.commit()
    # Emit success only after commit; no owner, source IDs, predicates or content.
    logger.info(
        "[intelligence] batch_committed mode=%s review=%s source_parts=%d messages_seen=%d findings=%d %s",
        "history" if run.get("knowledge_only") else "scheduled",
        review,
        len(batch),
        len({r["source_id"] for r in batch if r["kind"] == "message"}),
        len(findings),
        " ".join(f"{name}={value}" for name, value in counts.items()),
    )
    return counts


def defer_batch(owner, run, batch, code, *, review=False):
    """Retain rejected evidence, retry three times, then pause for explicit retry."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM intelligence_runs WHERE run_id=%s AND user_email=%s
            AND lease_token=%s AND lease_until > NOW() FOR UPDATE""",
            (run["run_id"], owner, run["lease_token"]),
        )
        if not cur.fetchone():
            raise ValueError("lease_expired")
        for row in batch:
            cur.execute(
                """INSERT INTO intelligence_failures
                (user_email, source_id, part, review, knowledge_only, error_code, next_attempt_at)
                VALUES (%s,%s,%s,%s,%s,%s,NOW() + INTERVAL '5 minutes')
                ON CONFLICT (user_email,source_id,part,review) DO UPDATE SET
                    attempts=LEAST(3,intelligence_failures.attempts+1),
                    error_code=EXCLUDED.error_code,
                    next_attempt_at=CASE WHEN intelligence_failures.attempts >= 2 THEN NULL
                        ELSE NOW() + INTERVAL '5 minutes' END""",
                (owner, row["source_id"], row["part"], review, run["knowledge_only"], code),
            )
        conn.commit()


def retry_failures(owner):
    """Replay retained failures and wake completed/failed runs for this owner only."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE intelligence_failures SET attempts=0,next_attempt_at=NOW() WHERE user_email=%s",
            (owner,),
        )
        count = cur.rowcount
        cur.execute(
            """UPDATE intelligence_runs r SET status='pending',next_attempt_at=NOW(),reviewed=FALSE
            WHERE r.user_email=%s AND r.status IN ('failed','succeeded') AND EXISTS (
                SELECT 1 FROM intelligence_failures f JOIN intelligence_sources s USING(source_id)
                WHERE f.user_email=r.user_email AND f.knowledge_only=r.knowledge_only AND s.observed_at<=r.cutoff)""",
            (owner,),
        )
        conn.commit()
        return count


def _save_knowledge(cur, owner, f):
    import knowledge

    # Serialize writes from scheduled analysis, chat learning and manual corrections.
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("knowledge:" + owner,))
    outcome = _save_knowledge_base(cur, owner, f)
    knowledge.decorate(cur, owner, f, outcome)
    return outcome


def _save_knowledge_base(cur, owner, f):
    ident = f.get("existing_id") or key(owner, f["subject_ref"], f["predicate"])
    cur.execute(
        """SELECT * FROM intelligence_knowledge WHERE user_email = %s
        AND ((%s::text IS NOT NULL AND knowledge_id = %s) OR (%s::text IS NULL AND subject_ref = %s AND predicate = %s)) FOR UPDATE""",
        (
            owner,
            f.get("existing_id"),
            ident,
            f.get("existing_id"),
            f["subject_ref"],
            f["predicate"],
        ),
    )
    old = cur.fetchone()
    import knowledge

    if not knowledge.permits_relearning(old, f["evidence"]):
        return "facts_protected"
    if old and old.get("legacy_fact_id") and not old["manual"]:
        # Reclassification may improve links without rewriting an imported current
        # statement from older evidence. Observation time is not applicability time.
        dates = [datetime.fromisoformat(e["sent_at"]) for e in f["evidence"] if e.get("sent_at")]
        if not dates or max(dates) <= old["updated_at"]:
            f = {**f, "statement": old["statement"], "status": old["status"]}
    if (
        old
        and old.get("legacy_fact_id")
        and not old["manual"]
        and old["subject_ref"] != f["subject_ref"]
    ):
        cur.execute(
            "INSERT INTO intelligence_knowledge_revisions(knowledge_id,snapshot) VALUES(%s,%s)",
            (old["knowledge_id"], json_value(dict(old))),
        )
        cur.execute(
            "UPDATE intelligence_knowledge SET subject_ref=%s,predicate=%s WHERE knowledge_id=%s",
            (f["subject_ref"], f["predicate"], old["knowledge_id"]),
        )
        old = {**old, "subject_ref": f["subject_ref"]}
    if old and (
        (old["manual"] and not old.get("deleted_at")) or old["subject_ref"] != f["subject_ref"]
    ):
        return "facts_protected"
    if old and old["status"] == "current" and f["status"] in {"uncertain", "historical"}:
        # A possible future change or a historical statement cannot replace a
        # settled current fact, even when it was mentioned more recently.
        return _save_knowledge(
            cur,
            owner,
            {**f, "existing_id": None, "predicate": f["predicate"][:85] + "." + f["status"]},
        )
    if old:
        ident = old["knowledge_id"]
        cur.execute(
            "INSERT INTO intelligence_knowledge_revisions(knowledge_id, snapshot) VALUES (%s, %s)",
            (ident, json_value(dict(old))),
        )
        evidence = (old["evidence"] + f["evidence"])[-64:]
        # Backlog imports and delayed old messages cannot silently roll current
        # knowledge back. Keep their evidence/revision without replacing it.
        earliest = datetime.min.replace(tzinfo=timezone.utc)
        newest_old = max(
            (datetime.fromisoformat(e["sent_at"]) for e in old["evidence"] if e.get("sent_at")),
            default=earliest,
        )
        newest_new = max(
            (datetime.fromisoformat(e["sent_at"]) for e in f["evidence"] if e.get("sent_at")),
            default=earliest,
        )
        if newest_new < newest_old:
            cur.execute(
                """INSERT INTO intelligence_knowledge_revisions(knowledge_id, snapshot)
                VALUES (%s,%s)""",
                (ident, json_value({**f, "status": "historical"})),
            )
            cur.execute(
                "UPDATE intelligence_knowledge SET evidence=%s WHERE knowledge_id=%s",
                (Jsonb(evidence), ident),
            )
            return "facts_supported"
        cur.execute(
            """UPDATE intelligence_knowledge SET statement = %s, status = %s,
            applicable_at = %s, valid_from=%s, valid_until=%s, evidence = %s, embedding=NULL,
            manual=CASE WHEN deleted_at IS NOT NULL THEN FALSE ELSE manual END,
            deleted_at=NULL, updated_at = NOW() WHERE knowledge_id = %s""",
            (
                f["statement"],
                f["status"],
                f.get("applicable_at"),
                f.get("valid_from"),
                f.get("valid_until"),
                Jsonb(evidence),
                ident,
            ),
        )
        return "facts_updated"
    else:
        cur.execute(
            """INSERT INTO intelligence_knowledge(knowledge_id, user_email, subject_ref,
            predicate, statement, status, applicable_at, valid_from, valid_until, context, evidence)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                ident,
                owner,
                f["subject_ref"],
                f["predicate"],
                f["statement"],
                f["status"],
                f.get("applicable_at"),
                f.get("valid_from"),
                f.get("valid_until"),
                f.get("context", "unclassified"),
                Jsonb(f["evidence"]),
            ),
        )
        return "facts_created"


def _save_proposal(cur, owner, f):
    # Semantic reconciliation happens before this controller-derived replay key.
    # An unchanged dismissed proposal must never resurface on a later run.
    dedupe = key(
        f["kind"],
        f["subject_ref"],
        re.sub(r"\W+", " ", f["statement"].casefold()).strip(),
        f.get("existing_id"),
        f.get("assignee_ref"),
        f.get("assignee_kind"),
        f.get("due_date"),
        f.get("project_id"),
        f.get("requested_status"),
    )
    cur.execute(
        """INSERT INTO intelligence_proposals(proposal_id, user_email, kind, dedupe_key,
        payload, evidence, reason) VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(user_email, dedupe_key) DO NOTHING""",
        (
            "intel_" + key(owner, dedupe),
            owner,
            f["kind"],
            dedupe,
            Jsonb({k: v for k, v in f.items() if k != "evidence"}),
            Jsonb(f["evidence"]),
            f.get("reason", ""),
        ),
    )
    return "proposals_created" if cur.rowcount else None


def _complete_task(cur, owner, f, task, batch, context):
    """Require explicit completion, an actor match and a fresh task version."""
    if not f.get("explicit_completion") or not f.get("verified_completion"):
        return
    refs = {r["source_id"]: r for r in batch}
    people = {p["identity_id"]: p for p in context["people"]}
    matching = []
    for e in f["evidence"]:
        row = refs[e["source_id"]]
        p = row["payload"]
        if row["kind"] != "message" or not row["actionable"] or p.get("forwarded", True):
            continue
        actor = people.get(p.get("identity_id"), {})
        cur.execute(
            "SELECT contact_id FROM messaging_identities WHERE identity_id=%s FOR SHARE",
            (p.get("identity_id"),),
        )
        current_actor = cur.fetchone()
        if current_actor:
            actor = dict(current_actor)
        kind, assignee = task["assignee_kind"], task.get("assignee_ref")
        matches = (
            (kind == "user" and p.get("outgoing"))
            or (kind == "identity" and assignee == p.get("identity_id"))
            or (kind == "contact" and assignee == actor.get("contact_id"))
        )
        if matches and datetime.fromisoformat(p["sent_at"]) >= task["created_at"]:
            # A manual reopening/edit after this evidence takes precedence.
            if (
                not task.get("intelligence_manual_at")
                or datetime.fromisoformat(p["sent_at"]) > task["intelligence_manual_at"]
            ):
                matching.append(e)
    if not matching:
        return
    if task.get("is_proposal"):
        cur.execute(
            """UPDATE intelligence_proposals SET status='expired', resolved_at=NOW(),
            resolution_evidence=%s WHERE user_email=%s AND proposal_id=%s
            AND status='pending' AND created_at=%s""",
            (Jsonb(matching), owner, task["todo_id"], task["created_at"]),
        )
        return "proposals_expired" if cur.rowcount else None
    cur.execute(
        """UPDATE todos SET status = 'completed', updated_at = NOW()
        WHERE todo_id = %s AND status = 'pending' AND updated_at = %s
            AND intelligence_manual_at IS NOT DISTINCT FROM %s
            AND (analysis_owner IS NULL OR analysis_owner = %s)""",
        (task["todo_id"], task["updated_at"], task.get("intelligence_manual_at"), owner),
    )
    if cur.rowcount:
        cur.execute(
            """INSERT INTO intelligence_task_evidence(todo_id, user_email, action, evidence)
            VALUES (%s,%s,'completed',%s)""",
            (task["todo_id"], owner, Jsonb(matching)),
        )
        return "tasks_completed"


def list_proposals(owner):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT p.*, COALESCE(c.display_name, i.display_name) AS assignee_name
            FROM intelligence_proposals p
            LEFT JOIN contacts c ON p.payload->>'assignee_kind'='contact'
                AND c.contact_id=p.payload->>'assignee_ref'
            LEFT JOIN messaging_identities i ON p.payload->>'assignee_kind'='identity'
                AND i.identity_id=p.payload->>'assignee_ref'
                AND i.account_id IN (SELECT account_id FROM messaging_accounts WHERE user_email=%s)
            WHERE p.user_email = %s AND p.status = 'pending'
            ORDER BY p.created_at DESC LIMIT 100""",
            (owner, owner),
        )
        return [
            {
                **dict(r),
                "payload": {**r["payload"], "assignee_name": r["assignee_name"]},
                "suggested_value": r["payload"]["statement"],
                "confidence": r["payload"].get("confidence", "high"),
                "contact_name": r["payload"].get("subject_ref"),
                "evidence": r["reason"],
                "source_evidence": r["evidence"],
            }
            for r in cur.fetchall()
        ]


def resolve_proposal(owner, proposal_id, *, accept, contact_resolutions=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM intelligence_proposals WHERE user_email = %s AND proposal_id = %s FOR UPDATE",
            (owner, proposal_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        if row["status"] != "pending":
            return {
                "accepted": row["status"] == "accepted",
                "reason": "already_resolved",
                "id": row["accepted_id"],
            }
        ident = None
        if accept:
            # Verify each cited revision still exists and is the current observed source.
            for e in row["evidence"]:
                cur.execute(
                    "SELECT * FROM intelligence_sources WHERE source_id = %s AND (owner_email IS NULL OR owner_email = %s)",
                    (e["source_id"], owner),
                )
                source = cur.fetchone()
                if (
                    not source
                    or not _current(cur, source, owner)
                    or source["payload"].get("deleted")
                ):
                    return {"accepted": False, "reason": "source_changed"}
            f = row["payload"]
            if row["kind"] == "contact_change":
                import contact_learning

                try:
                    result = contact_learning.accept(cur, owner, row, contact_resolutions)
                except ValueError:
                    return {"accepted": False, "reason": "contact_changed"}
                conn.commit()
                return result
            if f.get("project_id"):
                cur.execute(
                    "SELECT 1 FROM intelligence_projects WHERE project_id=%s AND user_email=%s FOR SHARE",
                    (f["project_id"], owner),
                )
                if not cur.fetchone():
                    return {"accepted": False, "reason": "related_item_changed"}
            if f["assignee_kind"] in {"contact", "identity"}:
                if f["assignee_kind"] == "contact":
                    cur.execute(
                        "SELECT 1 FROM contacts WHERE contact_id=%s FOR SHARE",
                        (f.get("assignee_ref"),),
                    )
                else:
                    cur.execute(
                        """SELECT 1 FROM messaging_identities i JOIN messaging_accounts a USING(account_id)
                        WHERE i.identity_id=%s AND a.user_email=%s FOR SHARE OF i""",
                        (f.get("assignee_ref"), owner),
                    )
                if not cur.fetchone():
                    return {"accepted": False, "reason": "related_item_changed"}
            cur.execute("SELECT set_config('brain.intelligence_write', 'on', TRUE)")
            ident = "intel_" + key(owner, proposal_id)
            if row["kind"] == "project":
                cur.execute(
                    """INSERT INTO intelligence_projects(project_id, user_email, title, description, evidence)
                    VALUES (%s,%s,%s,%s,%s)""",
                    (ident, owner, f["statement"], f.get("reason", ""), Jsonb(row["evidence"])),
                )
            elif row["kind"] == "task":
                cur.execute(
                    """INSERT INTO todos(todo_id, description, status, due_date,
                    assignee_kind, assignee_ref, project_id, analysis_owner)
                    VALUES (%s,%s,'pending',%s,%s,%s,%s,%s)""",
                    (
                        ident,
                        f["statement"],
                        f.get("due_date"),
                        f["assignee_kind"],
                        f.get("assignee_ref"),
                        f.get("project_id"),
                        owner,
                    ),
                )
                cur.execute(
                    """INSERT INTO intelligence_task_evidence(todo_id, user_email, action, evidence)
                    VALUES (%s,%s,'created',%s)""",
                    (ident, owner, Jsonb(row["evidence"])),
                )
            else:
                ident = f["existing_id"]
                # Proposed edits must be based on the still-current task snapshot.
                cur.execute(
                    """UPDATE todos SET description = %s, due_date = %s,
                    assignee_kind = %s, assignee_ref = %s, project_id = %s, status=%s,
                    intelligence_manual_at=NOW(), updated_at = NOW()
                    WHERE todo_id = %s AND updated_at = %s AND status = %s
                    AND (analysis_owner IS NULL OR analysis_owner = %s)""",
                    (
                        f["statement"],
                        f.get("due_date"),
                        f["assignee_kind"],
                        f.get("assignee_ref"),
                        f.get("project_id"),
                        f.get("requested_status", "pending"),
                        ident,
                        f.get("target_updated_at"),
                        f.get("target_status", "pending"),
                        owner,
                    ),
                )
                if not cur.rowcount:
                    return {"accepted": False, "reason": "task_changed"}
                cur.execute(
                    """INSERT INTO intelligence_task_evidence(todo_id, user_email, action, evidence)
                    VALUES (%s,%s,'changed',%s)""",
                    (ident, owner, Jsonb(row["evidence"])),
                )
        cur.execute(
            """UPDATE intelligence_proposals SET status = %s, accepted_id = %s,
            resolved_at = NOW() WHERE proposal_id = %s""",
            ("accepted" if accept else "dismissed", ident, proposal_id),
        )
        conn.commit()
        return {"accepted": accept, "id": ident}


def knowledge_for(owner, *, contact_id=None, query=None, limit=40):
    import knowledge

    subject = contact_ref(contact_id) if contact_id else None
    result = knowledge.list_items(owner, query=query, subject=subject, limit=limit)
    if contact_id:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM contacts, unnest(emails) address WHERE contact_id=%s AND lower(address)=lower(%s)",
                (contact_id, owner),
            )
            if cur.fetchone():
                result = list(
                    {
                        r["knowledge_id"]: r
                        for r in result
                        + knowledge.list_items(owner, query=query, subject="self", limit=limit)
                    }.values()
                )[:limit]
    return result


def briefing_digest(owner):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT statement, status FROM intelligence_knowledge WHERE user_email=%s AND deleted_at IS NULL
            AND updated_at >= NOW() - INTERVAL '1 day' AND status <> 'historical'
            AND (valid_until IS NULL OR valid_until >= NOW())
            ORDER BY updated_at DESC LIMIT 5""",
            (owner,),
        )
        knowledge = [dict(r) for r in cur.fetchall()]
        cur.execute(
            """SELECT payload->>'statement' AS statement, kind FROM intelligence_proposals
            WHERE user_email=%s AND status='pending' ORDER BY created_at DESC LIMIT 8""",
            (owner,),
        )
        proposals = [dict(r) for r in cur.fetchall()]
        cur.execute(
            """SELECT t.description FROM intelligence_task_evidence e JOIN todos t USING(todo_id)
            WHERE e.user_email=%s AND e.action='completed' AND t.status='completed'
            AND e.recorded_at >= NOW() - INTERVAL '1 day' ORDER BY e.recorded_at DESC LIMIT 5""",
            (owner,),
        )
        completed = [dict(r) for r in cur.fetchall()]
    return {"knowledge": knowledge, "proposals": proposals, "completed": completed}
