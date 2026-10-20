from __future__ import annotations

from typing import Any
from uuid import uuid4

from db import get_conn


def _create_proposal(
    *,
    user_email: str,
    dedupe_key: str,
    kind: str,
    contact_id: str,
    related_contact_id: str | None,
    suggested_value: str,
    reciprocal_value: str | None,
    source_event_id: str | None,
    confidence: str,
    evidence: str | None,
) -> bool:
    proposal_id = f"enrichment:{uuid4().hex}"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO contact_enrichment_proposals (
                proposal_id, user_email, dedupe_key, kind, contact_id,
                related_contact_id, suggested_value, reciprocal_value,
                source_event_id, confidence, evidence
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_email, dedupe_key) DO NOTHING
            """,
            (
                proposal_id,
                user_email,
                dedupe_key,
                kind,
                contact_id,
                related_contact_id,
                suggested_value,
                reciprocal_value,
                source_event_id,
                confidence if confidence in {"low", "medium", "high"} else "medium",
                evidence,
            ),
        )
        created = cur.rowcount > 0
        conn.commit()
    return created


def create_profession_proposal(
    *,
    user_email: str,
    contact_id: str,
    profession: str,
    source_event_id: str | None = None,
    confidence: str = "medium",
) -> bool:
    normalized = " ".join(str(profession or "").split()).strip()
    cleaned_contact_id = str(contact_id or "").strip()
    if not user_email or not cleaned_contact_id or not normalized:
        return False
    return _create_proposal(
        user_email=user_email,
        dedupe_key=f"profession:{cleaned_contact_id}:{normalized.casefold()}",
        kind="profession",
        contact_id=cleaned_contact_id,
        related_contact_id=None,
        suggested_value=normalized,
        reciprocal_value=None,
        source_event_id=source_event_id,
        confidence=confidence,
        evidence=None,
    )


def create_relationship_proposal(
    *,
    user_email: str,
    contact_id: str,
    related_contact_id: str,
    relationship_type: str,
    reciprocal_type: str | None = None,
    source_event_id: str | None = None,
    confidence: str = "medium",
    evidence: str | None = None,
) -> bool:
    first_id = str(contact_id or "").strip()
    second_id = str(related_contact_id or "").strip()
    label = " ".join(str(relationship_type or "").split()).strip()
    reciprocal = " ".join(str(reciprocal_type or "").split()).strip() or None
    if not user_email or not first_id or not second_id or first_id == second_id or not label:
        return False
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM contact_relationships
            WHERE (from_contact_id = %s AND to_contact_id = %s)
               OR (from_contact_id = %s AND to_contact_id = %s)
            LIMIT 1
            """,
            (first_id, second_id, second_id, first_id),
        )
        if cur.fetchone():
            conn.commit()
            return False
        conn.commit()
    pair = sorted((first_id, second_id))
    pair_labels = (label.casefold(), (reciprocal or "").casefold())
    canonical = sorted(pair_labels)
    return _create_proposal(
        user_email=user_email,
        dedupe_key=f"relationship:{pair[0]}:{pair[1]}:{canonical[0]}:{canonical[1]}",
        kind="relationship",
        contact_id=first_id,
        related_contact_id=second_id,
        suggested_value=label,
        reciprocal_value=reciprocal,
        source_event_id=source_event_id,
        confidence=confidence,
        evidence=(str(evidence or "").strip()[:240] or None),
    )


def list_proposals(user_email: str) -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                p.proposal_id, p.kind, p.status, p.contact_id,
                c.display_name AS contact_name,
                p.related_contact_id, related.display_name AS related_contact_name,
                p.suggested_value, p.reciprocal_value, p.source_event_id,
                e.title AS source_event_title, p.confidence, p.evidence,
                p.created_at, p.expires_at
            FROM contact_enrichment_proposals p
            JOIN contacts c ON c.contact_id = p.contact_id
            LEFT JOIN contacts related ON related.contact_id = p.related_contact_id
            LEFT JOIN events e ON e.id = p.source_event_id
            WHERE p.user_email = %s AND p.status = 'pending' AND p.expires_at > NOW()
            ORDER BY p.created_at DESC
            LIMIT 100
            """,
            (user_email,),
        )
        rows = cur.fetchall()
    return [dict(row) for row in rows]


def dismiss_proposal(user_email: str, proposal_id: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE contact_enrichment_proposals
            SET status = 'dismissed', updated_at = NOW()
            WHERE user_email = %s AND proposal_id = %s AND status = 'pending'
            """,
            (user_email, proposal_id),
        )
        changed = cur.rowcount > 0
        conn.commit()
    return changed


def accept_proposal(user_email: str, proposal_id: str) -> dict[str, Any] | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT kind, contact_id, related_contact_id, suggested_value,
                   reciprocal_value, status
            FROM contact_enrichment_proposals
            WHERE user_email = %s AND proposal_id = %s
            FOR UPDATE
            """,
            (user_email, proposal_id),
        )
        raw = cur.fetchone()
        if not raw:
            conn.commit()
            return None
        proposal = dict(raw)
        if proposal.get("status") != "pending":
            conn.commit()
            return {"accepted": False, "reason": "already_resolved"}

        if proposal["kind"] == "relationship":
            cur.execute(
                """
                SELECT relationship_id
                FROM contact_relationships
                WHERE (from_contact_id = %s AND to_contact_id = %s)
                   OR (from_contact_id = %s AND to_contact_id = %s)
                LIMIT 1
                """,
                (
                    proposal["contact_id"],
                    proposal["related_contact_id"],
                    proposal["related_contact_id"],
                    proposal["contact_id"],
                ),
            )
            if not cur.fetchone():
                cur.execute(
                    """
                    INSERT INTO contact_relationships (
                        relationship_id, from_contact_id, to_contact_id,
                        relationship_type, reciprocal_type
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (relationship_id) DO UPDATE SET
                        relationship_type = EXCLUDED.relationship_type,
                        reciprocal_type = EXCLUDED.reciprocal_type,
                        updated_at = NOW()
                    """,
                    (
                        f"rel:{proposal_id}",
                        proposal["contact_id"],
                        proposal["related_contact_id"],
                        proposal["suggested_value"],
                        proposal.get("reciprocal_value"),
                    ),
                )
        else:
            cur.execute(
                """
                SELECT display_name, aliases, tags, comments
                FROM contacts
                WHERE contact_id = %s
                FOR UPDATE
                """,
                (proposal["contact_id"],),
            )
            raw_contact = cur.fetchone()
            if not raw_contact:
                conn.commit()
                return None
            import knowledge

            knowledge.accept_profession(cur, user_email, proposal)

        cur.execute(
            """
            UPDATE contact_enrichment_proposals
            SET status = 'accepted', updated_at = NOW()
            WHERE user_email = %s AND proposal_id = %s AND status = 'pending'
            """,
            (user_email, proposal_id),
        )
        conn.commit()
    return {"accepted": True, "proposal_id": proposal_id}
