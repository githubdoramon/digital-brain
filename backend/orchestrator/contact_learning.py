"""Evidence-backed contact graph changes shared by every learning entry point.

The extractor describes one connected change. The controller owns identity checks,
replay receipts, automatic application and the review path, in one transaction.
"""

from __future__ import annotations

import re

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from birthdays import birthday_label, birthday_parts, birthday_value, parse_birthday


class PersonChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(pattern=r"^(contact:.+|new:[a-z0-9_.-]{1,80})$")
    display_name: str = Field(min_length=1, max_length=200)
    birthday: str | None = None

    @field_validator("birthday")
    @classmethod
    def valid_birthday(cls, value):
        parsed = parse_birthday(value)
        return parsed.isoformat() if hasattr(parsed, "isoformat") else parsed


class RelationshipChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_ref: str
    to_ref: str
    relationship_type: str = Field(min_length=1, max_length=80)
    reciprocal_type: str = Field(min_length=1, max_length=80)


class ContactChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    people: list[PersonChange] = Field(min_length=1, max_length=10)
    relationships: list[RelationshipChange] = Field(default_factory=list, max_length=10)


def _name(value):
    return " ".join(value.casefold().split())


def validate(finding, context, evidence):
    import knowledge

    graph = ContactChanges.model_validate(finding.get("contact_changes")).model_dump()
    if finding.get("inferred") and not finding.get("inference_reason"):
        raise ValueError("invalid_contact_change")
    known = {"contact:" + c["contact_id"]: c for c in context.get("contacts", [])}
    refs = {p["ref"] for p in graph["people"]}
    if len(refs) != len(graph["people"]):
        raise ValueError("invalid_contact_change")
    quotes = " ".join(e["quote"] for e in evidence)
    confirmed, anchored_contacts = [], []
    for person in graph["people"]:
        ref = person["ref"]
        if ref.startswith("contact:"):
            if ref not in known:
                raise ValueError("invalid_contact_change")
            person["display_name"] = known[ref]["display_name"]
            anchored = any(
                r.get("payload", {}).get("contact_id") == known[ref]["contact_id"]
                or known[ref]["contact_id"] in r.get("payload", {}).get("confirmed_contact_ids", [])
                for r in context.get("source_context", [])
            ) or any(
                p.get("contact_id") == known[ref]["contact_id"]
                and any(
                    r.get("payload", {}).get("identity_id") == p["identity_id"]
                    for r in context.get("source_context", [])
                )
                for p in context.get("people", [])
            )
            names = [known[ref]["display_name"], *(known[ref].get("aliases") or [])]
            full_name = any(len(n.split()) > 1 and _name(n) in _name(quotes) for n in names)
            if (anchored or full_name) and not knowledge.ambiguous_contact(
                {"subject_ref": ref}, context, evidence
            ):
                confirmed.append(ref)
            if anchored:
                anchored_contacts.append(ref)
        elif not re.search(r"(?<!\w)" + re.escape(person["display_name"]) + r"(?!\w)", quotes):
            raise ValueError("invalid_contact_change")
        if person.get("birthday") and not person["birthday"].startswith("--"):
            if not re.search(r"\b" + person["birthday"][:4] + r"\b", quotes):
                raise ValueError("invalid_contact_change")
    for rel in graph["relationships"]:
        if (
            rel["from_ref"] not in refs
            or rel["to_ref"] not in refs
            or rel["from_ref"] == rel["to_ref"]
        ):
            raise ValueError("invalid_contact_change")
        rel["relationship_type"] = rel["relationship_type"].strip().title()
        rel["reciprocal_type"] = rel["reciprocal_type"].strip().title()
        if not rel["relationship_type"] or not rel["reciprocal_type"]:
            raise ValueError("invalid_contact_change")
    return {
        **finding,
        "subject_ref": "general",
        "assignee_kind": "user",
        "assignee_ref": None,
        "existing_id": None,
        "project_id": None,
        "due_date": None,
        "valid_from": None,
        "valid_until": None,
        "contact_changes": graph,
        "confirmed_contacts": confirmed,
        "anchored_contacts": anchored_contacts,
    }


def _dedupe(finding):
    import intelligence

    graph = finding["contact_changes"]
    people = {p["ref"]: p for p in graph["people"]}

    def person_key(ref):
        if ref.startswith("contact:"):
            return ref
        anchors = []
        for r in graph["relationships"]:
            if r["from_ref"] == ref and r["to_ref"].startswith("contact:"):
                anchors.append((r["to_ref"], r["relationship_type"], r["reciprocal_type"]))
            if r["to_ref"] == ref and r["from_ref"].startswith("contact:"):
                anchors.append((r["from_ref"], r["reciprocal_type"], r["relationship_type"]))
        return intelligence.key(_name(people[ref]["display_name"]), sorted(anchors))

    names = {ref: person_key(ref) for ref in people}
    return intelligence.key(
        "contact_change",
        sorted((names[p["ref"]], p.get("birthday") or "") for p in people.values()),
        sorted(
            (names[r["from_ref"]], names[r["to_ref"]], r["relationship_type"], r["reciprocal_type"])
            for r in graph["relationships"]
        ),
    )


def _edges(cur, ids):
    cur.execute(
        """SELECT relationship_id,from_contact_id,to_contact_id,relationship_type,reciprocal_type
        FROM contact_relationships WHERE from_contact_id=ANY(%s) AND to_contact_id=ANY(%s)
        ORDER BY relationship_id FOR UPDATE""",
        (ids, ids),
    )
    return [dict(r) for r in cur.fetchall()]


def _pair(edge, source, target):
    if edge["from_contact_id"] == source and edge["to_contact_id"] == target:
        return edge["relationship_type"], edge.get("reciprocal_type")
    if edge["from_contact_id"] == target and edge["to_contact_id"] == source:
        return edge.get("reciprocal_type"), edge["relationship_type"]
    return None


def _plan(cur, owner, finding, resolutions=None):
    import intelligence

    graph = finding["contact_changes"]
    resolutions = resolutions or {}
    people = {p["ref"]: p for p in graph["people"]}
    if any(ref not in people for ref in resolutions):
        raise ValueError("invalid_contact_resolution")
    selected_ids = [v for v in resolutions.values() if v != "create"]
    ids = [r.removeprefix("contact:") for r in people if r.startswith("contact:")]
    first_names = list({_name(p["display_name"]).split()[0] for p in people.values()})
    cur.execute(
        """SELECT contact_id,display_name,aliases,birthday,birthday_month,birthday_day
        FROM contacts WHERE contact_id=ANY(%s) OR lower(split_part(display_name,' ',1))=ANY(%s)
            OR EXISTS(SELECT 1 FROM unnest(aliases) a WHERE lower(split_part(a,' ',1))=ANY(%s))
        ORDER BY (contact_id=ANY(%s)) DESC,contact_id LIMIT 200 FOR UPDATE""",
        (ids + selected_ids, first_names, first_names, ids + selected_ids),
    )
    contacts = {r["contact_id"]: dict(r) for r in cur.fetchall()}
    strong_existing = {
        ref
        for ref, person in people.items()
        if ref in finding.get("anchored_contacts", [])
        or (
            ref in finding["confirmed_contacts"]
            and sum(
                _name(c["display_name"]) == _name(person["display_name"]) for c in contacts.values()
            )
            == 1
        )
    }
    new_anchors = {
        ref
        for ref, person in people.items()
        if ref.startswith("new:")
        and len(person["display_name"].split()) > 1
        and not any(
            _name(c["display_name"]).split()[0] == _name(person["display_name"]).split()[0]
            for c in contacts.values()
        )
    }
    edges = _edges(cur, list(contacts))
    resolved, choices, reasons = {}, [], []
    has_changes = False
    if len(contacts) == 200:
        reasons.append("identity_uncertain")
    for ref, person in people.items():
        selection = resolutions.get(ref)
        original = ref.removeprefix("contact:") if ref.startswith("contact:") else None
        if original and original not in contacts:
            raise ValueError("contact_changed")
        matches = [
            c
            for c in contacts.values()
            if _name(c["display_name"]).split()[0] == _name(person["display_name"]).split()[0]
            or any(_name(a) == _name(person["display_name"]) for a in c.get("aliases") or [])
        ]
        if original and original not in {c["contact_id"] for c in matches}:
            matches.append(contacts[original])
        # A named child already linked to this exact parent is a stronger match than a name.
        related = set()
        for rel in graph["relationships"]:
            other = (
                rel["to_ref"]
                if rel["from_ref"] == ref
                else rel["from_ref"]
                if rel["to_ref"] == ref
                else ""
            )
            if not other.startswith("contact:") or (
                other not in strong_existing and other not in resolutions
            ):
                continue
            other_id = resolutions.get(other) or other.removeprefix("contact:")
            expected = (
                (rel["relationship_type"], rel["reciprocal_type"])
                if rel["from_ref"] == ref
                else (rel["reciprocal_type"], rel["relationship_type"])
            )
            related.update(
                c["contact_id"]
                for c in matches
                if any(_pair(e, c["contact_id"], other_id) == expected for e in edges)
            )
        exact_matches = [
            c for c in matches if _name(c["display_name"]) == _name(person["display_name"])
        ]
        identity_clear = (
            (ref in finding["confirmed_contacts"] and len(exact_matches) == 1)
            or ref in finding.get("anchored_contacts", [])
            or (original in related and len(related) == 1)
        )
        needs_choice = (original is not None and not identity_clear) or (
            original is None and bool(matches) and len(related) != 1
        )
        if selection:
            if selection == "create":
                if original:
                    raise ValueError("invalid_contact_resolution")
            elif selection not in contacts:
                raise ValueError("invalid_contact_resolution")
            target = selection if selection != "create" else None
        else:
            target = original or (next(iter(related)) if len(related) == 1 else None)
        if needs_choice and not selection:
            choices.append(
                {
                    "ref": ref,
                    "display_name": person["display_name"],
                    "can_create": original is None,
                    "candidates": [
                        {"contact_id": c["contact_id"], "display_name": c["display_name"]}
                        for c in matches
                    ],
                }
            )
            reasons.append("identity_uncertain")
        if (
            not original
            and not target
            and len(person["display_name"].split()) < 2
            and not any(
                (r["from_ref"] == ref and r["to_ref"] in strong_existing | new_anchors)
                or (r["to_ref"] == ref and r["from_ref"] in strong_existing | new_anchors)
                for r in graph["relationships"]
            )
        ):
            reasons.append("identity_uncertain")
        resolved[ref] = (
            target
            or "learned_"
            + intelligence.key(owner, _name(person["display_name"]), _dedupe(finding))[:32]
        )
        old = birthday_value(contacts.get(resolved[ref], {}))
        new = person.get("birthday")
        has_changes |= resolved[ref] not in contacts or bool(
            new and new != old and not (old and new.startswith("--") and old.endswith(new[1:]))
        )
        compatible = (
            old
            and new
            and (
                old == new
                or (new.startswith("--") and old.endswith(new[1:]))
                or (old.startswith("--") and new.endswith(old[1:]))
            )
        )
        if old and new and not compatible:
            reasons.append("birthday_conflict")
    if len(set(resolved.values())) != len(resolved):
        raise ValueError("invalid_contact_resolution")
    for rel in graph["relationships"]:
        pairs = [_pair(edge, resolved[rel["from_ref"]], resolved[rel["to_ref"]]) for edge in edges]
        expected_pair = (rel["relationship_type"], rel["reciprocal_type"])
        has_changes |= expected_pair not in pairs
        for edge in edges:
            pair = _pair(edge, resolved[rel["from_ref"]], resolved[rel["to_ref"]])
            if pair and pair != (rel["relationship_type"], rel["reciprocal_type"]):
                reasons.append("relationship_conflict")
    if finding.get("confidence") != "high" or finding.get("status") != "current":
        reasons.append("evidence_uncertain")
    snapshot = {
        "birthdays": {c: birthday_value(row) for c, row in contacts.items()},
        "relationships": edges,
    }
    return {
        "resolved": resolved,
        "choices": choices,
        "review_reasons": sorted(set(reasons)),
        "snapshot": snapshot,
        "has_changes": has_changes,
    }


def _apply(cur, owner, finding, plan, *, wake_deferred=True):
    import contacts
    import intelligence
    from schemas import ContactRelationshipIn

    graph = finding["contact_changes"]
    mapping = plan["resolved"]
    for person in graph["people"]:
        ident = mapping[person["ref"]]
        cur.execute(
            "INSERT INTO contacts(contact_id,display_name) VALUES(%s,%s) ON CONFLICT DO NOTHING",
            (ident, person["display_name"]),
        )
        value = person.get("birthday")
        old = plan["snapshot"]["birthdays"].get(ident)
        if value and not (old and value.startswith("--") and old.endswith(value[1:])):
            cur.execute(
                "UPDATE contacts SET birthday=%s,birthday_month=%s,birthday_day=%s WHERE contact_id=%s",
                (*birthday_parts(value), ident),
            )
        if value:
            intelligence._save_knowledge(
                cur,
                owner,
                {
                    **finding,
                    "kind": "knowledge",
                    "subject_ref": "contact:" + ident,
                    "related_subjects": [],
                    "entities": [],
                    "existing_id": None,
                    "predicate": "birthday",
                    "category": "biographical",
                    "status": "current",
                    "statement": f"{person['display_name']}'s birthday is {birthday_label(value)}.",
                },
            )
    for rel in graph["relationships"]:
        source, target = mapping[rel["from_ref"]], mapping[rel["to_ref"]]
        existing = next(
            (e for e in plan["snapshot"]["relationships"] if _pair(e, source, target)), None
        )
        rel_id = (
            existing["relationship_id"]
            if existing
            else "rel_learned_" + intelligence.key(*sorted([source, target]))[:32]
        )
        contacts._upsert_contact_relationships(
            cur,
            source,
            [
                ContactRelationshipIn(
                    relationship_id=rel_id,
                    from_contact_id=source,
                    to_contact_id=target,
                    relationship_type=rel["relationship_type"],
                    reciprocal_type=rel["reciprocal_type"],
                )
            ],
        )
        names = {p["ref"]: p["display_name"] for p in graph["people"]}
        intelligence._save_knowledge(
            cur,
            owner,
            {
                **finding,
                "kind": "knowledge",
                "subject_ref": "contact:" + source,
                "related_subjects": ["contact:" + target],
                "entities": [],
                "existing_id": None,
                "predicate": "relationship." + intelligence.key(*sorted([source, target]))[:24],
                "category": "biographical",
                "status": "current",
                "statement": f"{names[rel['from_ref']]} is {names[rel['to_ref']]}'s {rel['relationship_type'].lower()}.",
            },
        )
    if wake_deferred:
        cur.execute(
            """UPDATE knowledge_learning SET status='pending',attempts=0,next_attempt_at=NOW()
            WHERE user_email=%s AND source_id=ANY(%s) AND status='deferred'""",
            (owner, [e["source_id"] for e in finding["evidence"]]),
        )
    return mapping


def save(cur, owner, finding):
    import intelligence

    # Always acquire owner knowledge before the shared graph lock, including mixed batches.
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("knowledge:" + owner,))
    # Contacts are installation-shared, so simultaneous owners must not create duplicates.
    cur.execute("SELECT pg_advisory_xact_lock(732141980)")
    dedupe = _dedupe(finding)
    cur.execute(
        "SELECT 1 FROM intelligence_proposals WHERE user_email=%s AND dedupe_key=%s",
        (owner, dedupe),
    )
    if cur.fetchone():
        return None
    plan = _plan(cur, owner, finding)
    automatic = not plan["review_reasons"]
    payload = {**finding, "contact_plan": plan}
    result = (
        _apply(cur, owner, finding, plan, wake_deferred=plan["has_changes"]) if automatic else None
    )
    cur.execute(
        """INSERT INTO intelligence_proposals(proposal_id,user_email,kind,dedupe_key,payload,evidence,reason,
            status,accepted_id,resolved_at,resolution_evidence)
        VALUES(%s,%s,'contact_change',%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN NOW() END,%s)""",
        (
            "intel_" + intelligence.key(owner, dedupe),
            owner,
            dedupe,
            intelligence.json_value({k: v for k, v in payload.items() if k != "evidence"}),
            Jsonb(finding["evidence"]),
            finding.get("reason", ""),
            "accepted" if automatic else "pending",
            next(iter(result.values())) if result else None,
            automatic,
            Jsonb({"automatic": automatic, "contacts": result}),
        ),
    )
    return (
        ("contact_changes_applied" if plan["has_changes"] else "contact_changes_supported")
        if automatic
        else "proposals_created"
    )


def accept(cur, owner, row, resolutions=None):
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("knowledge:" + owner,))
    cur.execute("SELECT pg_advisory_xact_lock(732141980)")
    finding = {**row["payload"], "evidence": row["evidence"]}
    allowed = {
        c["ref"]: {v["contact_id"] for v in c["candidates"]}
        | ({"create"} if c["can_create"] else set())
        for c in finding["contact_plan"]["choices"]
    }
    if any(
        ref not in allowed or value not in allowed[ref]
        for ref, value in (resolutions or {}).items()
    ):
        return {"accepted": False, "reason": "contact_resolution_required"}
    plan = _plan(cur, owner, finding, resolutions)
    if plan["choices"]:
        return {"accepted": False, "reason": "contact_resolution_required"}
    previous = finding["contact_plan"]["snapshot"]
    for ident in plan["resolved"].values():
        if (
            ident in previous["birthdays"]
            and plan["snapshot"]["birthdays"].get(ident) != previous["birthdays"][ident]
        ):
            return {"accepted": False, "reason": "contact_changed"}

    def relevant(edges):
        return [
            e
            for e in edges
            if e["from_contact_id"] in plan["resolved"].values()
            and e["to_contact_id"] in plan["resolved"].values()
        ]

    if relevant(previous["relationships"]) != relevant(plan["snapshot"]["relationships"]):
        return {"accepted": False, "reason": "contact_changed"}
    result = _apply(cur, owner, finding, plan)
    cur.execute(
        "UPDATE intelligence_proposals SET status='accepted',accepted_id=%s,resolved_at=NOW(),resolution_evidence=%s WHERE proposal_id=%s",
        (
            next(iter(result.values())),
            Jsonb({"automatic": False, "contacts": result}),
            row["proposal_id"],
        ),
    )
    return {"accepted": True, "contacts": result}
