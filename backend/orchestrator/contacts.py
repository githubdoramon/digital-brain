from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import uuid4

from db import get_conn
from schemas import ContactIn, ContactRelationshipIn, ExternalPerson


__all__ = [
    "ingest_contact",
    "list_contacts",
    "get_contact",
    "get_contact_by_email",
    "get_contact_by_external_id",
    "sync_external_contact",
    "unlink_external_contact",
    "merge_contacts",
    "list_contact_merge_candidates",
    "delete_contact",
    "upsert_contact_relationship",
    "ensure_contact_for_email",
    "normalize_email",
]


def _upsert_contact_relationships(cur, contact_id: str, relationships: Sequence[Any]) -> None:
    if not relationships:
        return

    for rel in relationships:
        relationship_id = (
            getattr(rel, "relationship_id", None)
            or getattr(rel, "id", None)
            or f"rel_{contact_id}_{getattr(rel, 'to_contact_id', '')}"
        )
        from_id = getattr(rel, "from_contact_id", contact_id)
        to_id = getattr(rel, "to_contact_id", None)
        relationship_type = getattr(rel, "relationship_type", None) or getattr(rel, "type", None)
        reciprocal_type = getattr(rel, "reciprocal_type", None)

        if not to_id or not relationship_type:
            continue

        cur.execute(
            """
            INSERT INTO contact_relationships (
              relationship_id,
              from_contact_id,
              to_contact_id,
              relationship_type,
              reciprocal_type
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (relationship_id) DO UPDATE
              SET from_contact_id = EXCLUDED.from_contact_id,
                  to_contact_id = EXCLUDED.to_contact_id,
                  relationship_type = EXCLUDED.relationship_type,
                  reciprocal_type = EXCLUDED.reciprocal_type,
                  updated_at = NOW()
            """,
            (
                relationship_id,
                from_id,
                to_id,
                relationship_type,
                reciprocal_type,
            ),
        )


def ingest_contact(contact: ContactIn) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO contacts (
              contact_id,
              display_name,
              aliases,
              birthday,
              emails,
              phones,
              links,
              tags,
              external_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (contact_id) DO UPDATE
              SET display_name = EXCLUDED.display_name,
                  aliases = EXCLUDED.aliases,
                  birthday = EXCLUDED.birthday,
                  emails = EXCLUDED.emails,
                  phones = EXCLUDED.phones,
                  links = EXCLUDED.links,
                  tags = EXCLUDED.tags,
                  external_id = COALESCE(EXCLUDED.external_id, contacts.external_id)
            """,
            (
                contact.contact_id,
                contact.display_name,
                contact.aliases or [],
                contact.birthday,
                contact.emails or [],
                contact.phones or [],
                contact.links or [],
                contact.tags or [],
                getattr(contact, "external_id", None),
            ),
        )
        _upsert_contact_relationships(
            cur,
            contact.contact_id,
            getattr(contact, "relationships", []) or [],
        )
        conn.commit()


def list_contacts() -> List[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, birthday, emails, phones, links, tags, external_id
            FROM contacts
            ORDER BY display_name
            """
        )
        rows = cur.fetchall()
        relationships_map = _collect_contact_relationships()
        contacts: List[Dict[str, Any]] = []
        for row in rows:
            contact_id = row["contact_id"]
            contacts.append(
                {
                    "contact_id": contact_id,
                    "display_name": row["display_name"],
                    "aliases": row["aliases"] or [],
                    "birthday": row["birthday"].isoformat() if row["birthday"] else None,
                    "emails": row["emails"] or [],
                    "phones": row["phones"] or [],
                    "links": row["links"] or [],
                    "tags": row["tags"] or [],
                    "external_id": row["external_id"],
                    "relationships": relationships_map.get(contact_id, []),
                }
            )
        return contacts


def get_contact(contact_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, birthday, emails, phones, links, tags, external_id
            FROM contacts
            WHERE contact_id = %s
            """,
            (contact_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        relationships_map = _collect_contact_relationships([contact_id])
        return {
            "contact_id": row["contact_id"],
            "display_name": row["display_name"],
            "aliases": row["aliases"] or [],
            "birthday": row["birthday"].isoformat() if row["birthday"] else None,
            "emails": row["emails"] or [],
            "phones": row["phones"] or [],
            "links": row["links"] or [],
            "tags": row["tags"] or [],
            "external_id": row["external_id"],
            "relationships": relationships_map.get(contact_id, []),
        }


def get_contact_by_email(email: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_email(email)
    if not normalized:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, birthday, emails, phones, links, tags, external_id
            FROM contacts
            WHERE %s = ANY(emails)
            LIMIT 1
            """,
            (normalized,),
        )
        row = cur.fetchone()
        if not row:
            return None
        contact_id = row["contact_id"]
        relationships_map = _collect_contact_relationships([contact_id])
        return {
            "contact_id": contact_id,
            "display_name": row["display_name"],
            "aliases": row["aliases"] or [],
            "birthday": row["birthday"].isoformat() if row["birthday"] else None,
            "emails": row["emails"] or [],
            "phones": row["phones"] or [],
            "links": row["links"] or [],
            "tags": row["tags"] or [],
            "external_id": row["external_id"],
            "relationships": relationships_map.get(contact_id, []),
        }


def get_contact_by_external_id(external_id: str) -> Optional[Dict[str, Any]]:
    if not external_id:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id
            FROM contacts
            WHERE external_id = %s
            LIMIT 1
            """,
            (external_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return get_contact(row["contact_id"])


def _contact_exists(cur, contact_id: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM contacts
        WHERE contact_id = %s
        """,
        (contact_id,),
    )
    return cur.fetchone() is not None


def _fetch_contact_row(cur, contact_id: str):
    cur.execute(
        """
        SELECT contact_id, display_name, aliases, birthday, emails, phones, links, tags, external_id
        FROM contacts
        WHERE contact_id = %s
        """,
        (contact_id,),
    )
    return cur.fetchone()


def _merge_lists(*lists: Optional[Iterable[Any]]) -> List[str]:
    merged: List[str] = []
    for items in lists:
        if not items:
            continue
        for item in items:
            if item is None:
                continue
            text = str(item).strip()
            if not text:
                continue
            if text not in merged:
                merged.append(text)
    return merged


def _merge_emails(*lists: Optional[Iterable[str]]) -> List[str]:
    merged: List[str] = []
    for items in lists:
        if not items:
            continue
        for email in items:
            normalized = normalize_email(email)
            if not normalized:
                continue
            if normalized not in merged:
                merged.append(normalized)
    return merged


def _generate_external_contact_id(external_id: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", external_id.lower())
    safe = safe.strip("-") or uuid4().hex[:8]
    return f"contact:external:{safe}"


def _is_default_external_display(name: Optional[str], external_id: str) -> bool:
    if not name:
        return True
    expected = f"external contact {external_id}".strip()
    return name.strip().lower() == expected


def sync_external_contact(record: ExternalPerson, previous: Optional[ExternalPerson] = None) -> Dict[str, Any]:
    external_id = str(record.id).strip()
    if not external_id:
        raise ValueError("External contact id is required")

    preferred_name = record.name or (previous.name if previous else None)
    display_name = (preferred_name or f"External Contact {external_id}").strip()

    birthday = getattr(record, "birth_date", None) or getattr(previous, "birth_date", None)
    if isinstance(birthday, str):
        try:
            birthday = date.fromisoformat(birthday.split("T", 1)[0])
        except ValueError:
            birthday = None

    existing_row = None
    contact_id: Optional[str] = None

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id
            FROM contacts
            WHERE external_id = %s
            LIMIT 1
            """,
            (external_id,),
        )
        row = cur.fetchone()
        if row:
            contact_id = row["contact_id"]
            existing_row = _fetch_contact_row(cur, contact_id)
        else:
            normalized_display = display_name.lower()
            cur.execute(
                """
                SELECT contact_id
                FROM contacts
                WHERE external_id IS NULL
                  AND LOWER(display_name) = %s
                LIMIT 1
                """,
                (normalized_display,),
            )
            match = cur.fetchone()
            if match:
                contact_id = match["contact_id"]
                existing_row = _fetch_contact_row(cur, contact_id)

        if not contact_id:
            base_contact_id = _generate_external_contact_id(external_id)
            candidate = base_contact_id
            counter = 1
            while _contact_exists(cur, candidate):
                candidate = f"{base_contact_id}-{counter}"
                counter += 1
            contact_id = candidate

        existing_aliases = (existing_row["aliases"] if existing_row else []) or []
        existing_emails = (existing_row["emails"] if existing_row else []) or []
        existing_phones = (existing_row["phones"] if existing_row else []) or []
        existing_links = (existing_row["links"] if existing_row else []) or []
        existing_tags = (existing_row["tags"] if existing_row else []) or []
        existing_display = existing_row["display_name"] if existing_row else None
        existing_birthday = existing_row["birthday"] if existing_row else None

    merged_aliases = _merge_lists(existing_aliases)
    merged_emails = _merge_emails(existing_emails)
    merged_phones = _merge_lists(existing_phones)
    merged_links = _merge_lists(existing_links)
    merged_tags = _merge_lists(existing_tags)

    new_display_name = display_name.strip() if display_name else None
    final_display_name = existing_display
    if final_display_name:
        if (
            _is_default_external_display(final_display_name, external_id)
            and new_display_name
            and not _is_default_external_display(new_display_name, external_id)
        ):
            final_display_name = new_display_name
    else:
        final_display_name = new_display_name or f"External Contact {external_id}"

    final_birthday = existing_birthday or birthday

    ingest_contact(
        ContactIn(
            contact_id=contact_id,
            display_name=final_display_name,
            aliases=merged_aliases,
            birthday=final_birthday,
            emails=merged_emails,
            phones=merged_phones,
            links=merged_links,
            tags=merged_tags,
            external_id=external_id,
        )
    )

    return get_contact(contact_id)


def unlink_external_contact(external_id: str) -> bool:
    if not external_id:
        return False
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE contacts
            SET external_id = NULL,
                tags = (
                    SELECT array_agg(DISTINCT val)
                    FROM unnest(coalesce(tags, ARRAY[]::TEXT[]) || ARRAY['external-unlinked']) AS val
                )
            WHERE external_id = %s
            """,
            (external_id,),
        )
        updated = cur.rowcount > 0
        conn.commit()
        return updated


def merge_contacts(primary_contact_id: str, duplicate_contact_id: str) -> Dict[str, Any]:
    if primary_contact_id == duplicate_contact_id:
        raise ValueError("Cannot merge a contact with itself")

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, birthday, emails, phones, links, tags, external_id
            FROM contacts
            WHERE contact_id = ANY(%s)
            """,
            ([primary_contact_id, duplicate_contact_id],),
        )
        rows = cur.fetchall()
        if len(rows) < 2:
            raise LookupError("One or both contacts were not found for merge")

        row_map = {row["contact_id"]: row for row in rows}
        primary = row_map.get(primary_contact_id)
        duplicate = row_map.get(duplicate_contact_id)
        if not primary or not duplicate:
            raise LookupError("One or both contacts were not found for merge")

        merged_aliases = _merge_lists(primary["aliases"], duplicate["aliases"])
        merged_emails = _merge_emails(primary["emails"], duplicate["emails"])
        merged_phones = _merge_lists(primary["phones"], duplicate["phones"])
        merged_links = _merge_lists(primary["links"], duplicate["links"])
        merged_tags = _merge_lists(primary["tags"], duplicate["tags"])

        final_display_name = primary["display_name"] or duplicate["display_name"]
        final_birthday = primary["birthday"] or duplicate["birthday"]
        final_external_id = primary["external_id"] or duplicate["external_id"]

        if final_external_id and duplicate["external_id"] == final_external_id:
            cur.execute(
                """
                UPDATE contacts
                SET external_id = NULL
                WHERE contact_id = %s
                """,
                (duplicate_contact_id,),
            )

        cur.execute(
            """
            UPDATE contacts
            SET display_name = %s,
                aliases = %s,
                birthday = %s,
                emails = %s,
                phones = %s,
                links = %s,
                tags = %s,
                external_id = %s
            WHERE contact_id = %s
            """,
            (
                final_display_name,
                merged_aliases,
                final_birthday,
                merged_emails,
                merged_phones,
                merged_links,
                merged_tags,
                final_external_id,
                primary_contact_id,
            ),
        )

        # Update relationships
        cur.execute(
            """
            UPDATE contact_relationships
            SET from_contact_id = %s
            WHERE from_contact_id = %s
            """,
            (primary_contact_id, duplicate_contact_id),
        )
        cur.execute(
            """
            UPDATE contact_relationships
            SET to_contact_id = %s
            WHERE to_contact_id = %s
            """,
            (primary_contact_id, duplicate_contact_id),
        )

        # Update todo linkages
        cur.execute(
            """
            SELECT todo_id
            FROM todo_contacts
            WHERE contact_id = %s
            """,
            (duplicate_contact_id,),
        )
        todo_rows = cur.fetchall()
        for todo_row in todo_rows:
            todo_id = todo_row["todo_id"]
            cur.execute(
                """
                INSERT INTO todo_contacts (todo_id, contact_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (todo_id, primary_contact_id),
            )
        if todo_rows:
            cur.execute(
                """
                DELETE FROM todo_contacts
                WHERE contact_id = %s
                """,
                (duplicate_contact_id,),
            )

        # Update events people arrays
        cur.execute(
            """
            UPDATE events
            SET people = (
                SELECT array_agg(DISTINCT elem)
                FROM unnest(array_replace(coalesce(people, ARRAY[]::TEXT[]), %s, %s)) AS elem
            )
            WHERE people @> ARRAY[%s]::TEXT[]
            """,
            (duplicate_contact_id, primary_contact_id, duplicate_contact_id),
        )

        cur.execute(
            """
            DELETE FROM contacts
            WHERE contact_id = %s
            """,
            (duplicate_contact_id,),
        )

        conn.commit()

    return get_contact(primary_contact_id)


def list_contact_merge_candidates() -> Dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, emails, phones, links, tags, external_id
            FROM contacts
            ORDER BY display_name
            """
        )
        rows = cur.fetchall()

    def _serialize(row) -> Dict[str, Any]:
        return {
            "contact_id": row["contact_id"],
            "display_name": row["display_name"],
            "aliases": row["aliases"] or [],
            "emails": row["emails"] or [],
            "phones": row["phones"] or [],
            "links": row["links"] or [],
            "tags": row["tags"] or [],
            "external_id": row["external_id"],
        }

    external_contacts: List[Dict[str, Any]] = []
    unlinked_contacts: List[Dict[str, Any]] = []
    for row in rows:
        serialized = _serialize(row)
        if row["external_id"]:
            external_contacts.append(serialized)
        else:
            unlinked_contacts.append(serialized)

    from rapidfuzz import fuzz

    suggestions: List[Dict[str, Any]] = []
    for external in external_contacts:
        source_name_candidates = [
            external.get("display_name") or "",
            *external.get("aliases", []),
        ]
        source_candidates = [name for name in source_name_candidates if name]
        if not source_candidates:
            continue

        best_score = -1
        best_target: Optional[Dict[str, Any]] = None
        best_match_name: Optional[str] = None

        for candidate_name in source_candidates:
            for target in unlinked_contacts:
                target_names = [target.get("display_name") or "", *target.get("aliases", [])]
                for target_name in target_names:
                    if not target_name:
                        continue
                    score = fuzz.token_sort_ratio(candidate_name, target_name)
                    if score > best_score:
                        best_score = score
                        best_target = target
                        best_match_name = target_name

        if best_target and best_score >= 78:
            suggestions.append(
                {
                    "external_contact_id": external["contact_id"],
                    "external_display_name": external.get("display_name"),
                    "candidate_contact_id": best_target["contact_id"],
                    "candidate_display_name": best_target.get("display_name"),
                    "score": best_score,
                    "matched_on": best_match_name,
                }
            )

    return {
        "external_contacts": external_contacts,
        "unlinked_contacts": unlinked_contacts,
        "suggestions": suggestions,
    }


def normalize_email(email: str) -> Optional[str]:
    if not email:
        return None
    cleaned = email.strip().lower()
    return cleaned or None


def _display_name_from_email(email: str) -> str:
    local_part = email.split("@", 1)[0] if "@" in email else email
    pieces = [piece for piece in re.split(r"[._+]+", local_part) if piece]
    if not pieces:
        return email
    return " ".join(piece.capitalize() for piece in pieces)


def _generate_contact_id(email: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", email)
    safe = safe.strip("-") or uuid4().hex[:8]
    return f"contact:{safe}"


def ensure_contact_for_email(email: str) -> Tuple[Optional[str], bool]:
    normalized = normalize_email(email)
    if not normalized:
        return None, False
    existing = get_contact_by_email(normalized)
    if existing:
        return existing["contact_id"], False

    base_contact_id = _generate_contact_id(normalized)
    contact_id = base_contact_id
    counter = 1
    with get_conn() as conn, conn.cursor() as cur:
        while _contact_exists(cur, contact_id):
            contact_id = f"{base_contact_id}-{counter}"
            counter += 1

    contact = ContactIn(
        contact_id=contact_id,
        display_name=_display_name_from_email(normalized),
        emails=[normalized],
        aliases=[],
        tags=["autocreated", "meeting-attendee"],
    )
    ingest_contact(contact)
    return contact_id, True


def delete_contact(contact_id: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM contacts
            WHERE contact_id = %s
            """,
            (contact_id,),
        )
        deleted = cur.rowcount > 0
        conn.commit()
        return deleted


def upsert_contact_relationship(rel: ContactRelationshipIn) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO contact_relationships (
              relationship_id,
              from_contact_id,
              to_contact_id,
              relationship_type,
              reciprocal_type
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (relationship_id) DO UPDATE
              SET from_contact_id = EXCLUDED.from_contact_id,
                  to_contact_id = EXCLUDED.to_contact_id,
                  relationship_type = EXCLUDED.relationship_type,
                  reciprocal_type = EXCLUDED.reciprocal_type,
                  updated_at = NOW()
            """,
            (
                rel.relationship_id,
                rel.from_contact_id,
                rel.to_contact_id,
                rel.relationship_type,
                rel.reciprocal_type,
            ),
        )
        conn.commit()


def _collect_contact_relationships(contact_ids: Optional[Iterable[str]] = None) -> Dict[str, List[Dict[str, Any]]]:
    conditions = []
    params: List[Any] = []
    if contact_ids:
        contact_list = list(contact_ids)
        if contact_list:
            conditions.append("from_contact_id = ANY(%s)")
            conditions.append("to_contact_id = ANY(%s)")
            params.extend([contact_list, contact_list])
        else:
            return {}

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " OR ".join(conditions)

    query = f"""
        SELECT
          relationship_id,
          from_contact_id,
          to_contact_id,
          relationship_type,
          reciprocal_type,
          created_at,
          updated_at
        FROM contact_relationships
        {where_clause}
        ORDER BY created_at ASC
    """

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, tuple(params))
        rows = cur.fetchall()

    relationships_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        created_at = row["created_at"].isoformat() if row["created_at"] else None
        updated_at = row["updated_at"].isoformat() if row["updated_at"] else None

        relationships_map[row["from_contact_id"]].append(
            {
                "relationship_id": row["relationship_id"],
                "contact_id": row["to_contact_id"],
                "type": row["relationship_type"],
                "other_type": row["reciprocal_type"],
                "direction": "outgoing",
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )

        inverse_type = row["reciprocal_type"] or row["relationship_type"]
        relationships_map[row["to_contact_id"]].append(
            {
                "relationship_id": row["relationship_id"],
                "contact_id": row["from_contact_id"],
                "type": inverse_type,
                "other_type": row["relationship_type"],
                "direction": "incoming",
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )

    return dict(relationships_map)
