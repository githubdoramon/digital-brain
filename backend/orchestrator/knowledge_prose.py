"""Human-readable learned prose; stable references remain in structured fields."""

import re

from knowledge_references import contact_ref

PROSE_INSTRUCTIONS = """Write statements as concise, self-contained human prose, usually one or two
sentences. Use people's supplied display names, retaining attribution and uncertainty.
Internal contact/identity/entity/knowledge IDs belong ONLY in structured reference
fields, never in statements, inference reasons or summaries, including parentheses.
Do not add related-knowledge pointers, raw messaging mention IDs, repeated source
quotes or commentary about the extraction process to a statement. Keep evidence
quotes exact in the evidence fields. Do not infer a person's identity from a name.
"""

_REF = re.compile(
    r"(?<![\w/:=#?&])(?:(?:contact|identity|entity):[A-Za-z0-9_:-]+(?:\.[A-Za-z0-9_:-]+)*"
    r"|identity\s+[0-9a-f]{32,64})\b",
    re.IGNORECASE,
)
_RELATED = re.compile(r"\s*\(see related knowledge [a-z0-9_-]{8,80}\)", re.IGNORECASE)


def _canonical(ref):
    ref = re.sub(r"^identity\s+", "identity:", ref, flags=re.IGNORECASE)
    prefix, _, ident = ref.partition(":")
    return prefix.lower() + ":" + ident


def clean(text, labels=None):
    """Remove metadata annotations; replace inline refs without guessing identities.

    This applies only to derived prose, never original source quotes or manual edits.
    Unknown people retain a neutral description and their structured identity link.
    """
    if not text:
        return text
    labels = labels or {}

    def annotation(match):
        if not _REF.search(match[1]):
            return match[0]
        remaining = _REF.sub("", match[1])
        return "" if not remaining.strip(" ,;:/\t\n") else match[0]

    text = re.sub(r"\s*\(([^()]*)\)", annotation, text)
    text = _RELATED.sub("", text)

    def replace(match):
        ref = _canonical(match[0])
        return labels.get(ref) or ("this subject" if ref.startswith("entity:") else "this person")

    text = _REF.sub(replace, text)
    return re.sub(r"[ \t]+([,.;!?])", r"\1", re.sub(r"[ \t]{2,}", " ", text)).strip()


def context_labels(context):
    labels = {
        contact_ref(c["contact_id"]): c.get("display_name") for c in context.get("contacts", [])
    }
    labels.update(
        {"identity:" + p["identity_id"]: p.get("display_name") for p in context.get("people", [])}
    )
    labels.update({e["entity_ref"]: e.get("label") for e in context.get("entities", [])})
    return labels


def needs_repair(text):
    return bool(text and (_REF.search(text) or _RELATED.search(text)))


def clean_rows(cur, owner, rows, *, field="statement"):
    """Prepare derived prose for persistence; callers own revisions and commits."""
    refs = list(
        {
            _canonical(match[0])
            for row in rows
            if not row.get("manual")
            for match in _REF.finditer(row.get(field) or "")
        }
    )
    labels = {}
    if refs:
        cur.execute(
            """SELECT 'contact:'||contact_id AS ref,display_name AS label FROM contacts
            WHERE 'contact:'||contact_id=ANY(%s)
            UNION ALL SELECT 'identity:'||i.identity_id,COALESCE(c.display_name,i.display_name)
            FROM messaging_identities i JOIN messaging_accounts a USING(account_id)
            LEFT JOIN contacts c ON c.contact_id=i.contact_id
            WHERE a.user_email=%s AND 'identity:'||i.identity_id=ANY(%s)
            UNION ALL SELECT entity_ref,label FROM knowledge_entities
            WHERE user_email=%s AND entity_ref=ANY(%s)""",
            (refs, owner, refs, owner, refs),
        )
        labels = {row["ref"]: row["label"] for row in cur.fetchall()}
    for row in rows:
        if not row.get("manual"):
            row[field] = clean(row.get(field), labels)
    return rows
