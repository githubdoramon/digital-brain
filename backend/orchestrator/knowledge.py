"""Shared, owner-scoped knowledge. Sources and controller validation own truth boundaries."""

from __future__ import annotations

import math
import re
from datetime import datetime
from functools import lru_cache

from db import get_conn
from knowledge_references import contact_ref, normalize_finding
from learning_validation import reject

KINDS = {
    "person",
    "organization",
    "product",
    "device",
    "software",
    "game",
    "topic",
    "place",
    "project",
    "event",
    "document",
}
CATEGORIES = {
    "preference",
    "biographical",
    "behavioral",
    "goal",
    "opinion",
    "constraint",
    "general",
    "experience",
    "observation",
    "compatibility",
    "decision",
}


def validate_entities(finding, context, evidence):
    """New non-person entities require a literal source label; people require resolved IDs."""
    import intelligence

    finding = normalize_finding(finding, context)
    known = {"self", "general"}
    known.update(contact_ref(c["contact_id"]) for c in context.get("contacts", []))
    known.update("identity:" + p["identity_id"] for p in context.get("people", []))
    known.update(e["entity_ref"] for e in context.get("entities", []))
    mapping = {}
    entities = []
    quotes = "\n".join(e["quote"] for e in evidence).casefold()
    subjects = [finding["subject_ref"], *finding.get("related_subjects", [])]
    for entity in finding.get("entities", []):
        ref, kind, label = (
            entity.get("ref", ""),
            entity.get("kind"),
            entity.get("label", "").strip(),
        )
        # Only entities linked by this finding can be persisted. Extra definitions
        # from other candidates do not invalidate an otherwise supported fact.
        if ref not in subjects:
            continue
        if not re.fullmatch(r"new:[a-z0-9_.-]{1,80}", ref) or kind not in KINDS - {"person"}:
            reject("invalid_subject", "invalid_entity_definition", entity=entity, finding=finding)
        if not 2 <= len(label) <= 200 or label.casefold() not in quotes:
            reject(
                "invalid_subject",
                "entity_label_not_in_cited_evidence",
                entity=entity,
                finding=finding,
                evidence=evidence,
            )
        canonical = "entity:" + intelligence.key(kind, label.casefold())[:32]
        if ref in mapping:
            reject("invalid_subject", "duplicate_entity_ref", entity=entity, finding=finding)
        mapping[ref] = canonical
        entities.append({"entity_ref": canonical, "kind": kind, "label": label})
    if len(subjects) > 12 or any(s not in known and s not in mapping for s in subjects):
        reject(
            "invalid_subject",
            "unknown_subject_ref",
            finding=finding,
            rejected_refs=[s for s in subjects if s not in known and s not in mapping],
            allowed_refs=sorted(known),
            declared_entity_refs=list(mapping),
        )
    canonical_subjects = [mapping.get(s, s) for s in subjects]
    entities.extend(e for e in context.get("entities", []) if e["entity_ref"] in canonical_subjects)
    entities.extend(
        {"entity_ref": contact_ref(c["contact_id"]), "kind": "person", "label": c["display_name"]}
        for c in context.get("contacts", [])
        if contact_ref(c["contact_id"]) in canonical_subjects
    )
    return {
        "subject_ref": mapping.get(subjects[0], subjects[0]),
        "related_subjects": list(dict.fromkeys(mapping.get(s, s) for s in subjects[1:])),
        "entity_records": entities,
    }


def decorate(cur, owner, finding, outcome):
    if outcome not in {"facts_created", "facts_updated"}:
        return
    cur.execute(
        "SELECT knowledge_id FROM intelligence_knowledge WHERE user_email=%s AND (knowledge_id=%s OR (subject_ref=%s AND predicate=%s)) ORDER BY (knowledge_id=%s) DESC LIMIT 1",
        (
            owner,
            finding.get("existing_id"),
            finding["subject_ref"],
            finding["predicate"],
            finding.get("existing_id"),
        ),
    )
    row = cur.fetchone()
    if not row:
        return
    ident = row["knowledge_id"]
    for entity in finding.get("entity_records", []):
        cur.execute(
            "INSERT INTO knowledge_entities VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (owner, entity["entity_ref"], entity["kind"], entity["label"]),
        )
    cur.execute(
        """UPDATE intelligence_knowledge SET category=%s,inferred=%s,inference_reason=%s,
        revision=revision+1,deleted_at=NULL WHERE knowledge_id=%s""",
        (
            finding.get("category", "general"),
            finding.get("inferred", False),
            finding.get("inference_reason"),
            ident,
        ),
    )
    refs = list(dict.fromkeys([finding["subject_ref"], *finding.get("related_subjects", [])]))
    cur.execute(
        "DELETE FROM knowledge_subjects WHERE knowledge_id=%s AND NOT(subject_ref=ANY(%s))",
        (ident, refs),
    )
    for ref in refs:
        cur.execute(
            "INSERT INTO knowledge_subjects VALUES(%s,%s) ON CONFLICT DO NOTHING", (ident, ref)
        )


def permits_relearning(old, evidence):
    if not old or not old.get("deleted_at"):
        return True
    # New ingestion IDs for historical imports are not new events. Require material evidence
    # that actually occurred after deletion (or an explicitly revised source after deletion).
    boundary = old["deleted_at"]
    for e in evidence:
        for date in (e.get("effective_at"), e.get("sent_at")):
            try:
                if date and datetime.fromisoformat(str(date)) > boundary:
                    return True
            except (ValueError, TypeError):
                pass
    return False


def change(owner, ident, *, statement=None, category=None, importance=None, delete=False):
    if category is not None and category not in CATEGORIES:
        raise ValueError("invalid_category")
    if statement is not None and not 3 <= len(statement.strip()) <= 2000:
        raise ValueError("invalid_statement")
    import intelligence

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("knowledge:" + owner,))
        cur.execute(
            "SELECT * FROM intelligence_knowledge WHERE user_email=%s AND knowledge_id=%s AND deleted_at IS NULL FOR UPDATE",
            (owner, ident),
        )
        old = cur.fetchone()
        if not old:
            return None
        cur.execute(
            "INSERT INTO intelligence_knowledge_revisions(knowledge_id,snapshot) VALUES(%s,%s)",
            (ident, intelligence.json_value(dict(old))),
        )
        cur.execute(
            """UPDATE intelligence_knowledge SET statement=COALESCE(%s,statement),
            category=COALESCE(%s,category),importance=COALESCE(%s,importance),manual=TRUE,
            inferred=CASE WHEN %s::text IS NULL THEN inferred ELSE FALSE END,
            inference_reason=CASE WHEN %s::text IS NULL THEN inference_reason ELSE NULL END,
            revision=revision+1,embedding=NULL,deleted_at=CASE WHEN %s THEN NOW() ELSE NULL END,
            updated_at=NOW() WHERE knowledge_id=%s RETURNING *""",
            (statement, category, importance, statement, statement, delete, ident),
        )
        updated = dict(cur.fetchone())
        conn.commit()
        return updated


@lru_cache(maxsize=128)
def _vector_for(text):
    from embeddings import embed_text

    result = embed_text(text, log_response_body=False, request_timeout=(1, 3))
    if not result or not all(math.isfinite(v) for v in result) or not any(result):
        raise ValueError("embedding_unavailable")
    return tuple(result)


def vector_for(text):
    try:
        return _vector_for(text)
    except Exception:
        return None


def list_items(
    owner, *, query=None, subject=None, limit=40, offset=0, include_deleted=False, profile=False
):
    """One lexical/entity search with semantic fallback; all paths retain owner boundaries."""
    import intelligence

    words = intelligence._query(query or "")
    vector = list(vector_for(query) or []) if query else []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT k.*, CASE WHEN k.status='current' AND k.valid_until < NOW()
            THEN 'historical' ELSE k.status END AS status,
            ARRAY(SELECT subject_ref FROM knowledge_subjects WHERE knowledge_id=k.knowledge_id) AS subjects
            FROM intelligence_knowledge k
            WHERE k.user_email=%s AND (%s OR k.deleted_at IS NULL)
            AND (NOT %s OR k.category NOT IN ('experience','observation','compatibility','decision'))
            AND (%s::text IS NULL OR EXISTS(SELECT 1 FROM knowledge_subjects s WHERE s.knowledge_id=k.knowledge_id AND
                (s.subject_ref=%s OR s.subject_ref IN (SELECT 'identity:'||i.identity_id
                    FROM messaging_identities i JOIN messaging_accounts a USING(account_id)
                    WHERE a.user_email=%s AND 'contact:'||i.contact_id=%s)
                OR (s.subject_ref='self' AND EXISTS(SELECT 1 FROM contacts c,unnest(c.emails) address
                    WHERE 'contact:'||c.contact_id=%s AND lower(address)=lower(%s))))))
            AND (%s::text IS NULL OR k.search_text @@ to_tsquery('simple',%s)
                OR knowledge_cosine(k.embedding,%s::float8[]) >= 0.65
                OR EXISTS(SELECT 1 FROM knowledge_subjects s JOIN knowledge_entities e
                    ON e.user_email=k.user_email AND e.entity_ref=s.subject_ref
                    WHERE s.knowledge_id=k.knowledge_id AND to_tsvector('simple',e.label) @@ to_tsquery('simple',%s)))
            ORDER BY GREATEST(COALESCE(knowledge_cosine(k.embedding,%s::float8[]),0), ts_rank(k.search_text,to_tsquery('simple',%s))) DESC,k.manual DESC,k.importance DESC,k.updated_at DESC,k.knowledge_id LIMIT %s OFFSET %s""",
            (
                owner,
                include_deleted,
                profile,
                subject,
                subject,
                owner,
                subject,
                subject,
                owner,
                query,
                words,
                vector,
                words,
                vector,
                words,
                limit,
                offset,
            ),
        )
        rows = [dict(r) for r in cur.fetchall()]
        for row in rows:
            row.pop("embedding", None)
            row.pop("search_text", None)
        return rows


def entities_for(owner, query=None, limit=100):
    import intelligence

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT CASE WHEN i.contact_id IS NOT NULL THEN 'contact:'||i.contact_id ELSE s.subject_ref END AS entity_ref,
            CASE WHEN s.subject_ref='self' THEN 'Me' ELSE COALESCE(c.display_name,linked.display_name,i.display_name,e.label,'Topic') END AS label,
            COALESCE(e.kind,CASE WHEN s.subject_ref LIKE 'contact:%%' OR s.subject_ref LIKE 'identity:%%' OR s.subject_ref='self' THEN 'person' ELSE 'topic' END) AS kind
            FROM knowledge_subjects s JOIN intelligence_knowledge k USING(knowledge_id)
            LEFT JOIN knowledge_entities e ON e.user_email=k.user_email AND e.entity_ref=s.subject_ref
            LEFT JOIN contacts c ON 'contact:'||c.contact_id=s.subject_ref
            LEFT JOIN messaging_identities i ON 'identity:'||i.identity_id=s.subject_ref AND i.account_id IN (SELECT account_id FROM messaging_accounts WHERE user_email=k.user_email)
            LEFT JOIN contacts linked ON linked.contact_id=i.contact_id
            WHERE k.user_email=%s AND k.deleted_at IS NULL
            AND (%s::text IS NULL OR to_tsvector('simple',COALESCE(c.display_name,linked.display_name,i.display_name,e.label,'Me')) @@ to_tsquery('simple',%s))
            ORDER BY label LIMIT %s""",
            (owner, query, intelligence._query(query or ""), limit),
        )
        return [dict(r) for r in cur.fetchall()]


def summary_for(owner, subject):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT summary,version,rendered_version FROM knowledge_summaries
            WHERE user_email=%s AND subject_ref=%s""",
            (owner, subject),
        )
        row = cur.fetchone()
        return dict(row) if row else {"summary": None, "version": 0, "rendered_version": 0}


def source_is_current(cur, row, owner):
    """Return None for existing archive kinds, otherwise validate the new source revision."""
    if row.get("payload", {}).get("learning_owner") or row.get("payload", {}).get("origin") == "confirmed_contact_command":
        return False
    kind = row["kind"]
    if kind not in {"chat", "contact_note", "legacy_fact", "manual_fact"}:
        return None
    if kind == "chat":
        cur.execute(
            """SELECT m.content AS text FROM conversation_messages m JOIN conversation_threads t
            ON t.id=m.thread_id WHERE m.message_id::text=%s AND t.user_email=%s AND m.role='user' FOR SHARE OF m""",
            (row["entity_id"], owner),
        )
    elif kind == "contact_note":
        cur.execute(
            "SELECT comments AS text FROM contacts WHERE contact_id=%s FOR SHARE",
            (row["entity_id"],),
        )
    else:
        return row["owner_email"] == owner
    current = cur.fetchone()
    return bool(current and current["text"] == row["payload"].get("text"))


def ambiguous_contact(finding, context, evidence):
    """A shared first name is insufficient to attach a fact to one of several contacts."""
    finding = normalize_finding(finding, context)
    refs = [finding.get("subject_ref", ""), *finding.get("related_subjects", [])]
    text = " ".join(e["quote"] for e in evidence).casefold()
    people = context.get("contacts", [])
    for ref in refs:
        if not ref.startswith("contact:"):
            continue
        target = next((c for c in people if contact_ref(c["contact_id"]) == ref), None)
        if not target:
            continue
        if any(
            r.get("payload", {}).get("contact_id") == target["contact_id"]
            for r in context.get("source_context", [])
        ):
            continue
        full = target["display_name"].casefold()
        if full in text:
            continue
        first = full.split()[0] if full.split() else ""
        if (
            first
            and re.search(r"\b" + re.escape(first) + r"\b", text)
            and sum(c["display_name"].casefold().split()[:1] == [first] for c in people) > 1
        ):
            return True
    return False


def contact_matches(owner, query, limit=40):
    """Owner-private role/company knowledge augments, never replaces, identity matching."""
    result = {}
    for row in list_items(owner, query=query, limit=limit):
        if row["status"] != "current":
            continue
        for ref in row["subjects"]:
            if ref.startswith("contact:"):
                result[ref.removeprefix("contact:")] = row
            elif ref.startswith("identity:"):
                with get_conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        """SELECT i.contact_id FROM messaging_identities i JOIN messaging_accounts a USING(account_id)
                        WHERE a.user_email=%s AND i.identity_id=%s AND i.contact_id IS NOT NULL""",
                        (owner, ref.removeprefix("identity:")),
                    )
                    linked = cur.fetchone()
                    if linked:
                        result[linked["contact_id"]] = row
    return result


def accept_profession(cur, owner, proposal):
    import intelligence

    text = "Profession: " + proposal["suggested_value"]
    source_ref = proposal["proposal_id"]
    cur.execute(
        "SELECT intelligence_store_source('manual_fact',%s,%s,%s)",
        (
            source_ref,
            owner,
            intelligence.json_value(
                {
                    "text": text,
                    "contact_id": proposal["contact_id"],
                    "sent_at": intelligence.utcnow().isoformat(),
                }
            ),
        ),
    )
    cur.execute(
        "SELECT * FROM intelligence_sources WHERE kind='manual_fact' AND entity_id=%s AND owner_email=%s ORDER BY source_id DESC LIMIT 1",
        (source_ref, owner),
    )
    source = cur.fetchone()
    f = {
        "subject_ref": contact_ref(proposal["contact_id"]),
        "predicate": "profession",
        "statement": proposal["suggested_value"],
        "status": "current",
        "category": "biographical",
        "context": "unclassified",
        "existing_id": None,
        "evidence": [
            {
                "source_id": source["source_id"],
                "part": 0,
                "quote": text,
                "kind": "manual_fact",
                "entity_id": source_ref,
                "sent_at": source["payload"]["sent_at"],
            }
        ],
    }
    intelligence._save_knowledge(cur, owner, f)


def relink_contacts(cur, primary, duplicate):
    """Carry canonical subjects and provider links through a confirmed contact merge."""
    import intelligence

    old_ref, new_ref = contact_ref(duplicate), contact_ref(primary)
    cur.execute(
        "SELECT DISTINCT k.user_email FROM intelligence_knowledge k JOIN knowledge_subjects s USING(knowledge_id) WHERE s.subject_ref=%s",
        (old_ref,),
    )
    owners = [r["user_email"] for r in cur.fetchall()]
    for owner in sorted(owners):
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("knowledge:" + owner,))
        cur.execute(
            "SELECT k.* FROM intelligence_knowledge k JOIN knowledge_subjects s USING(knowledge_id) WHERE k.user_email=%s AND s.subject_ref=%s FOR UPDATE OF k",
            (owner, old_ref),
        )
        for row in cur.fetchall():
            cur.execute(
                "INSERT INTO intelligence_knowledge_revisions(knowledge_id,snapshot) VALUES(%s,%s)",
                (row["knowledge_id"], intelligence.json_value(dict(row))),
            )
            if row["subject_ref"] == old_ref:
                cur.execute(
                    "SELECT 1 FROM intelligence_knowledge WHERE user_email=%s AND subject_ref=%s AND predicate=%s",
                    (owner, new_ref, row["predicate"]),
                )
                predicate = (
                    row["predicate"]
                    if not cur.fetchone()
                    else row["predicate"][:65] + ".merged." + row["knowledge_id"][:20]
                )
                cur.execute(
                    "UPDATE intelligence_knowledge SET subject_ref=%s,predicate=%s,revision=revision+1 WHERE knowledge_id=%s",
                    (new_ref, predicate, row["knowledge_id"]),
                )
            cur.execute(
                "INSERT INTO knowledge_subjects VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (row["knowledge_id"], new_ref),
            )
            cur.execute(
                "DELETE FROM knowledge_subjects WHERE knowledge_id=%s AND subject_ref=%s",
                (row["knowledge_id"], old_ref),
            )
    cur.execute(
        "UPDATE messaging_identities SET contact_id=%s WHERE contact_id=%s", (primary, duplicate)
    )
