"""Typed knowledge references wrap opaque IDs; existing stored links stay stable."""

from __future__ import annotations


def contact_ref(contact_id):
    # An opaque contact ID may itself start with contact:. Do not strip it.
    return "contact:" + contact_id


def resolve_contact_ref(value, contacts, *, known_refs=()):
    canonical = {contact_ref(c["contact_id"]) for c in contacts} | set(known_refs)
    if value in canonical:
        return value
    # Accept only an exact supplied opaque ID, never a label or a guessed suffix.
    aliases = {contact_ref(c["contact_id"]) for c in contacts if c["contact_id"] == value}
    return (
        next(iter(aliases))
        if isinstance(value, str) and value.startswith("contact:") and len(aliases) == 1
        else value
    )


def normalize_finding(finding, context):
    contacts = context.get("contacts", [])
    known_refs = {entity["entity_ref"] for entity in context.get("entities", [])}

    def resolve(value):
        return resolve_contact_ref(value, contacts, known_refs=known_refs)

    result = {
        **finding,
        "subject_ref": resolve(finding.get("subject_ref")),
    }
    if "related_subjects" in finding:
        result["related_subjects"] = list(
            dict.fromkeys(resolve(ref) for ref in finding["related_subjects"])
        )
    if finding.get("assignee_kind") == "contact":
        value = finding.get("assignee_ref")
        ids = {c["contact_id"] for c in contacts}
        # Assignees store opaque IDs, whereas subject/person refs are typed.
        if value not in ids:
            matches = {c["contact_id"] for c in contacts if contact_ref(c["contact_id"]) == value}
            if len(matches) == 1:
                result["assignee_ref"] = next(iter(matches))
    if isinstance(graph := finding.get("contact_changes"), dict):
        result["contact_changes"] = {
            **graph,
            "people": [{**p, "ref": resolve(p["ref"])} for p in graph.get("people", [])],
            "relationships": [
                {**r, "from_ref": resolve(r["from_ref"]), "to_ref": resolve(r["to_ref"])}
                for r in graph.get("relationships", [])
            ],
        }
    return result
