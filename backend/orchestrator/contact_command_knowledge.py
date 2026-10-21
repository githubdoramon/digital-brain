"""Record reviewed contact effects directly; never re-extract an executed command."""

from datetime import datetime, timezone

import intelligence
from birthdays import birthday_label
from db import get_conn
from knowledge_references import contact_ref


def record_confirmation(owner, preview_id, command_data, proposal, contact_ids):
    """One durable receipt and knowledge writes, with original user text as evidence."""
    original = str(command_data.get("original_message") or "")
    messages = command_data.get("conversation_messages") or [{"role": "user", "content": original}]
    texts = [m["content"] for m in messages if m.get("role") == "user" and m.get("content")]
    if not texts:
        texts = [original]
    request_time = command_data.get("request_time") or {}
    sent_at = request_time.get("sent_at") or datetime.now(timezone.utc).isoformat()
    facts = []
    names = {}
    for item in proposal.get("contacts", []):
        ref = item.get("reference")
        ident = contact_ids.get(ref, ref)
        if not ident:
            continue
        merged = item.get("merged") or {}
        name = merged.get("display_name") or item.get("display_name") or "The contact"
        names[ref] = name
        names[ident] = name
        fields = item.get("fields") or {}
        if "birthday" in fields and merged.get("birthday"):
            facts.append(
                (
                    ident,
                    "birthday",
                    f"{name}'s birthday is {birthday_label(merged['birthday'])}.",
                    [],
                    False,
                )
            )
        if fields.get("profession"):
            facts.append(
                (ident, "employment", f"{name}'s profession is {fields['profession']}.", [], False)
            )
    for rel in proposal.get("relationships", []) + proposal.get("derived_relationships", []):
        source, target = rel["from_reference"], rel["to_reference"]
        source_id, target_id = contact_ids.get(source, source), contact_ids.get(target, target)
        source_name = names.get(source) or rel.get("from_display_name") or "The contact"
        target_name = names.get(target) or rel.get("to_display_name") or "the other contact"
        role = rel["relationship_type"]
        facts.append(
            (
                source_id,
                "relationship." + intelligence.key(*sorted([source_id, target_id]))[:24],
                f"{source_name} is {target_name}'s {role}.",
                [contact_ref(target_id)],
                rel.get("source") == "derived",
            )
        )
    place_names = {place.get("reference"): place.get("name") for place in proposal.get("places", [])}
    for link in proposal.get("contact_place_links", []):
        source = link["contact_reference"]
        ident = contact_ids.get(source, source)
        name = names.get(source) or link.get("contact_display_name") or "The contact"
        place = place_names.get(link.get("place_reference")) or link.get("place_name")
        role = link.get("role") or "associated place"
        if place:
            facts.append(
                (
                    ident,
                    "place." + intelligence.key(role)[:24],
                    f"{name}'s {role} is {place}.",
                    [],
                    False,
                )
            )
    for fact in proposal.get("additional_knowledge", []):
        ident = contact_ids.get(fact["reference"], fact["reference"])
        facts.append((ident, fact["predicate"], fact["statement"], [], False))

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("knowledge:" + owner,))
        receipt_id = "contact-command:" + preview_id
        cur.execute(
            "SELECT 1 FROM intelligence_sources WHERE kind='manual_fact' AND entity_id=%s AND owner_email=%s",
            (receipt_id, owner),
        )
        if cur.fetchone():
            return
        payload = {
            "text": "\n\n".join(texts),
            "sent_at": sent_at,
            "timezone": request_time.get("timezone"),
            "origin": "confirmed_contact_command",
            "learning_owner": "contact",
            "confirmed_contact_ids": sorted(set(contact_ids.values())),
            "approved_effects": proposal,
        }
        cur.execute(
            "SELECT intelligence_store_source('manual_fact',%s,%s,%s)",
            (receipt_id, owner, intelligence.json_value(payload)),
        )
        cur.execute(
            "SELECT source_id FROM intelligence_sources WHERE kind='manual_fact' AND entity_id=%s AND owner_email=%s ORDER BY source_id DESC LIMIT 1",
            (receipt_id, owner),
        )
        source_id = cur.fetchone()["source_id"]
        evidence = [
            {
                "source_id": source_id,
                "part": 0,
                "quote": text,
                "kind": "manual_fact",
                "entity_id": receipt_id,
                "sent_at": sent_at,
                "effective_at": datetime.now(timezone.utc).isoformat(),
            }
            for text in texts
        ]
        for ident, predicate, statement, related, inferred in facts:
            ref = contact_ref(ident)
            cur.execute("SELECT display_name FROM contacts WHERE contact_id=%s", (ident,))
            row = cur.fetchone()
            label = row["display_name"] if row else names.get(ident, "Contact")
            # A user-confirmed correction supersedes the previous manually maintained value.
            cur.execute(
                "UPDATE intelligence_knowledge SET manual=FALSE WHERE user_email=%s AND subject_ref=%s AND predicate=%s",
                (owner, ref, predicate),
            )
            intelligence._save_knowledge(
                cur,
                owner,
                {
                    "kind": "knowledge",
                    "user_confirmed": True,
                    "subject_ref": ref,
                    "predicate": predicate,
                    "statement": statement,
                    "status": "current",
                    "context": "personal",
                    "category": "biographical",
                    "related_subjects": related,
                    "evidence": evidence,
                    "inferred": inferred,
                    "inference_reason": "Derived relationship reviewed during contact confirmation."
                    if inferred
                    else None,
                    "entity_records": [{"entity_ref": ref, "kind": "person", "label": label}],
                },
            )
            cur.execute(
                "UPDATE intelligence_knowledge SET manual=TRUE WHERE user_email=%s AND subject_ref=%s AND predicate=%s",
                (owner, ref, predicate),
            )
        conn.commit()
