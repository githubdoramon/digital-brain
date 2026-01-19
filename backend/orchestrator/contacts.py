from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any
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
    # Smart contact lookup functions
    "search_contacts",
    "get_contact_relationships",
    "find_related_contacts",
    # Relationship resolution functions
    "get_relationship_type_mappings",
    "find_related_types",
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
              comments,
              external_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (contact_id) DO UPDATE
              SET display_name = EXCLUDED.display_name,
                  aliases = EXCLUDED.aliases,
                  birthday = EXCLUDED.birthday,
                  emails = EXCLUDED.emails,
                  phones = EXCLUDED.phones,
                  links = EXCLUDED.links,
                  tags = EXCLUDED.tags,
                  comments = COALESCE(EXCLUDED.comments, contacts.comments),
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
                contact.comments,
                getattr(contact, "external_id", None),
            ),
        )
        _upsert_contact_relationships(
            cur,
            contact.contact_id,
            getattr(contact, "relationships", []) or [],
        )
        conn.commit()


def list_contacts() -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, birthday, emails, phones, links, tags, comments, external_id
            FROM contacts
            ORDER BY display_name
            """
        )
        rows = cur.fetchall()
        relationships_map = _collect_contact_relationships()
        contacts: list[dict[str, Any]] = []
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
                    "comments": row["comments"] or "",
                    "external_id": row["external_id"],
                    "relationships": relationships_map.get(contact_id, []),
                }
            )
        return contacts


def get_contact(contact_id: str) -> dict[str, Any] | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, birthday, emails, phones, links, tags, comments, external_id
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
            "comments": row["comments"] or "",
            "external_id": row["external_id"],
            "relationships": relationships_map.get(contact_id, []),
        }


def get_contact_by_email(email: str) -> dict[str, Any] | None:
    normalized = normalize_email(email)
    if not normalized:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, birthday, emails, phones, links, tags, comments, external_id
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
            "comments": row["comments"] or "",
            "external_id": row["external_id"],
            "relationships": relationships_map.get(contact_id, []),
        }


def get_contact_by_external_id(external_id: str) -> dict[str, Any] | None:
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
        SELECT contact_id, display_name, aliases, birthday, emails, phones, links, tags, comments, external_id
        FROM contacts
        WHERE contact_id = %s
        """,
        (contact_id,),
    )
    return cur.fetchone()


def _merge_lists(*lists: Iterable[Any] | None) -> list[str]:
    merged: list[str] = []
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


def _merge_emails(*lists: Iterable[str] | None) -> list[str]:
    merged: list[str] = []
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


def _merge_comments(primary: str | None, duplicate: str | None) -> str | None:
    primary_text = (primary or "").strip()
    duplicate_text = (duplicate or "").strip()
    if not primary_text and not duplicate_text:
        return None
    if not primary_text:
        return duplicate_text
    if not duplicate_text:
        return primary_text
    if primary_text == duplicate_text:
        return primary_text
    return f"{primary_text}\n\n{duplicate_text}"


def _generate_external_contact_id(external_id: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", external_id.lower())
    safe = safe.strip("-") or uuid4().hex[:8]
    return f"contact:external:{safe}"


def _is_default_external_display(name: str | None, external_id: str) -> bool:
    if not name:
        return True
    expected = f"external contact {external_id}".strip()
    return name.strip().lower() == expected


def sync_external_contact(record: ExternalPerson, previous: ExternalPerson | None = None) -> dict[str, Any]:
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
    contact_id: str | None = None

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
        existing_comments = existing_row["comments"] if existing_row else None
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
            comments=existing_comments,
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


def merge_contacts(primary_contact_id: str, duplicate_contact_id: str) -> dict[str, Any]:
    if primary_contact_id == duplicate_contact_id:
        raise ValueError("Cannot merge a contact with itself")

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, birthday, emails, phones, links, tags, comments, external_id
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
        merged_comments = _merge_comments(primary.get("comments"), duplicate.get("comments"))

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
                comments = %s,
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
                merged_comments,
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


def list_contact_merge_candidates() -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, emails, phones, links, tags, external_id
            FROM contacts
            ORDER BY display_name
            """
        )
        rows = cur.fetchall()

    def _serialize(row) -> dict[str, Any]:
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

    external_contacts: list[dict[str, Any]] = []
    unlinked_contacts: list[dict[str, Any]] = []
    for row in rows:
        serialized = _serialize(row)
        if row["external_id"]:
            external_contacts.append(serialized)
        else:
            unlinked_contacts.append(serialized)

    from rapidfuzz import fuzz

    suggestions: list[dict[str, Any]] = []
    for external in external_contacts:
        source_name_candidates = [
            external.get("display_name") or "",
            *external.get("aliases", []),
        ]
        source_candidates = [name for name in source_name_candidates if name]
        if not source_candidates:
            continue

        best_score = -1
        best_target: dict[str, Any] | None = None
        best_match_name: str | None = None

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


def normalize_email(email: str) -> str | None:
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


def ensure_contact_for_email(email: str) -> tuple[str | None, bool]:
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


def find_self_contact(email: str) -> dict[str, Any] | None:
    """
    Find the user's own contact record by email.

    Used by the LLM prompt builder to get context about the user.

    Args:
        email: The user's email address

    Returns:
        Contact dict if found, None otherwise
    """
    return get_contact_by_email(email)


def resolve_query(query: str) -> dict[str, Any]:
    """
    Extract structured entities from a natural-language query.

    Attempts to find contacts, places, and time ranges mentioned in the query.

    Args:
        query: The natural-language query to parse

    Returns:
        Dict with 'contacts', 'places', and 'time_range' keys
    """
    # Simple implementation: search for contacts by name matching
    contacts_found: list[dict[str, Any]] = []
    places_found: list[dict[str, Any]] = []

    # Get all contacts and do fuzzy matching
    all_contacts = list_contacts()
    query_lower = query.lower()

    for contact in all_contacts:
        display_name = (contact.get("display_name") or "").lower()
        aliases = [a.lower() for a in (contact.get("aliases") or [])]
        comments = (contact.get("comments") or "").lower()

        # Check if any name appears in the query
        if display_name and display_name in query_lower:
            contacts_found.append(contact)
        elif any(alias in query_lower for alias in aliases):
            contacts_found.append(contact)
        elif comments and query_lower in comments:
            contacts_found.append(contact)

    return {
        "contacts": contacts_found,
        "places": places_found,
        "time_range": None,
    }


def search_contacts(
    query: str,
    *,
    search_by: str = "any",
    fuzzy_threshold: int = 75,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Smart contact search with fuzzy matching and multiple search modes.

    Handles:
    - Partial names, nicknames, aliases
    - Case-insensitive matching
    - Fuzzy matching for typos and variations
    - Email and phone lookups

    Args:
        query: Search string (name, email, phone, or any)
        search_by: "name", "email", "phone", or "any" (default)
        fuzzy_threshold: Minimum fuzzy match score (0-100, default 75)
        limit: Maximum results to return

    Returns:
        List of matching contacts with match_score and match_reason
    """
    from rapidfuzz import fuzz

    query_lower = query.strip().lower()
    if not query_lower:
        return []

    all_contacts = list_contacts()
    matches: list[tuple[dict[str, Any], int, str]] = []

    for contact in all_contacts:
        best_score = 0
        match_reason = ""

        # Search by email
        if search_by in ("email", "any"):
            emails = contact.get("emails") or []
            for email in emails:
                email_lower = email.lower()
                if query_lower in email_lower:
                    score = 100 if query_lower == email_lower else 90
                    if score > best_score:
                        best_score = score
                        match_reason = f"email match: {email}"

        # Search by phone
        if search_by in ("phone", "any"):
            phones = contact.get("phones") or []
            query_digits = "".join(c for c in query_lower if c.isdigit())
            if query_digits:
                for phone in phones:
                    phone_digits = "".join(c for c in phone if c.isdigit())
                    if query_digits in phone_digits:
                        score = 100 if query_digits == phone_digits else 85
                        if score > best_score:
                            best_score = score
                            match_reason = f"phone match: {phone}"

        # Search by name (display_name + aliases)
        if search_by in ("name", "any"):
            display_name = (contact.get("display_name") or "").lower()
            aliases = [a.lower() for a in (contact.get("aliases") or [])]
            all_names = [display_name] + aliases

            for name in all_names:
                if not name:
                    continue

                # Exact match
                if query_lower == name:
                    if 100 > best_score:
                        best_score = 100
                        match_reason = f"exact name match: {name}"
                    continue

                # Substring match: query in name (user searching "Ed", contact "Eduardo")
                # This is always OK - the user is searching for part of the contact's name
                if query_lower in name:
                    score = 95
                    if score > best_score:
                        best_score = score
                        match_reason = f"name contains: {name}"
                    continue

                # Substring match: name in query (user searching "Pedro", contact "Ed")
                # This is ONLY OK if the name is a complete word in a multi-word query
                # We DON'T want "Ed" to match "Pedro" (random substring)
                # We DO want "Ed" to match "Ed Smith" (complete word in multi-word query)
                if name in query_lower:
                    # Only match if query has spaces (multi-word) and name is a complete word
                    if ' ' in query_lower:
                        name_len = len(name)
                        name_pos = query_lower.find(name)

                        # Check if name appears as a complete word (surrounded by spaces or at edges)
                        at_start = name_pos == 0
                        at_end = name_pos + name_len == len(query_lower)
                        preceded_by_space = name_pos > 0 and query_lower[name_pos - 1] == ' '
                        followed_by_space = name_pos + name_len < len(query_lower) and query_lower[name_pos + name_len] == ' '

                        # Must be at start/end with space on other side, or surrounded by spaces
                        is_complete_word = (at_start and followed_by_space) or (at_end and preceded_by_space) or (preceded_by_space and followed_by_space)

                        if is_complete_word:
                            score = 90
                            if score > best_score:
                                best_score = score
                                match_reason = f"query contains name: {name}"
                            continue

                # First name / last name partial match
                name_parts = name.split()
                query_parts = query_lower.split()
                for qpart in query_parts:
                    for npart in name_parts:
                        if qpart == npart:
                            score = 88
                            if score > best_score:
                                best_score = score
                                match_reason = f"name part match: {npart}"

                # Fuzzy match
                fuzzy_score = fuzz.token_sort_ratio(query_lower, name)
                if fuzzy_score >= fuzzy_threshold and fuzzy_score > best_score:
                    best_score = fuzzy_score
                    match_reason = f"fuzzy match ({fuzzy_score}%): {name}"

        if search_by == "any":
            comments = (contact.get("comments") or "").lower()
            if comments and query_lower in comments:
                score = 80
                if score > best_score:
                    best_score = score
                    match_reason = "comment match"

        if best_score >= fuzzy_threshold:
            matches.append((contact, best_score, match_reason))

    # Sort by score descending, then by display_name
    matches.sort(key=lambda x: (-x[1], x[0].get("display_name", "")))

    # Return top matches with score and reason
    results = []
    for contact, score, reason in matches[:limit]:
        result = dict(contact)
        result["match_score"] = score
        result["match_reason"] = reason
        results.append(result)

    return results


def get_contact_relationships(
    contact_id: str,
    *,
    relationship_types: list[str] | None = None,
    include_contact_details: bool = True,
) -> dict[str, Any]:
    """
    Get a contact's relationships with optional filtering.

    Args:
        contact_id: The contact to get relationships for
        relationship_types: Filter by specific relationship types (optional - LLM decides what's relevant)
        include_contact_details: Include full contact info for related contacts

    Returns:
        Dict with contact info and all relationships (LLM filters based on context)
    """
    contact = get_contact(contact_id)
    if not contact:
        return {"error": f"Contact not found: {contact_id}", "found": False}

    relationships = contact.get("relationships") or []

    # Build filter set if specific types requested
    allowed_types: set[str] | None = None
    if relationship_types:
        allowed_types = {t.lower() for t in relationship_types}

    # Process relationships
    result_relationships = []
    for rel in relationships:
        rel_type = (rel.get("type") or "").lower()
        other_type = (rel.get("other_type") or "").lower()

        # Check if any type matches the filter (if filter provided)
        if allowed_types is not None:
            if rel_type not in allowed_types and other_type not in allowed_types:
                continue

        filtered_rel = dict(rel)

        # Include related contact details if requested
        if include_contact_details:
            related_contact_id = rel.get("contact_id")
            if related_contact_id:
                related_contact = get_contact(related_contact_id)
                if related_contact:
                    filtered_rel["related_contact"] = {
                        "display_name": related_contact.get("display_name"),
                        "emails": related_contact.get("emails"),
                        "phones": related_contact.get("phones"),
                        "tags": related_contact.get("tags"),
                    }

        result_relationships.append(filtered_rel)

    return {
        "found": True,
        "contact": {
            "contact_id": contact["contact_id"],
            "display_name": contact["display_name"],
            "aliases": contact.get("aliases", []),
            "emails": contact.get("emails", []),
            "phones": contact.get("phones", []),
            "tags": contact.get("tags", []),
            "comments": contact.get("comments", ""),
        },
        "relationships": result_relationships,
        "relationship_count": len(result_relationships),
        "filter_applied": relationship_types if relationship_types else None,
    }


def find_related_contacts(
    query: str,
    *,
    relationship_types: list[str] | None = None,
    fuzzy_threshold: int = 75,
) -> dict[str, Any]:
    """
    Find a contact by query and return their related contacts.

    This is a high-level function that:
    1. Searches for the contact using fuzzy matching
    2. Finds all related contacts
    3. Optionally filters by relationship types (LLM decides based on user query)

    Args:
        query: Search string to find the primary contact
        relationship_types: Specific relationship types to filter (optional)
        fuzzy_threshold: Minimum match score for contact search

    Returns:
        Dict with primary contact and their related contacts
    """
    # First, find the primary contact
    matches = search_contacts(query, fuzzy_threshold=fuzzy_threshold, limit=1)

    if not matches:
        return {
            "found": False,
            "error": f"No contact found matching '{query}'",
            "suggestions": _get_search_suggestions(query),
        }

    primary_contact = matches[0]
    contact_id = primary_contact["contact_id"]

    # Get relationships with optional filtering
    relationships_result = get_contact_relationships(
        contact_id,
        relationship_types=relationship_types,
        include_contact_details=True,
    )

    return {
        "found": True,
        "primary_contact": {
            "contact_id": primary_contact["contact_id"],
            "display_name": primary_contact["display_name"],
            "match_score": primary_contact.get("match_score"),
            "match_reason": primary_contact.get("match_reason"),
        },
        "related_contacts": relationships_result.get("relationships", []),
        "relationship_count": relationships_result.get("relationship_count", 0),
        "filter_applied": relationships_result.get("filter_applied"),
    }


def _get_search_suggestions(query: str) -> list[str]:
    """Generate suggestions when no contacts match."""
    query_lower = query.lower()
    all_contacts = list_contacts()

    # Get contacts that partially match
    partial_matches = []
    for contact in all_contacts:
        display_name = (contact.get("display_name") or "").lower()
        if any(part in display_name for part in query_lower.split()):
            partial_matches.append(contact["display_name"])

    if partial_matches:
        return [f"Did you mean: {', '.join(partial_matches[:3])}?"]

    return ["Try searching with a different name or check the spelling."]


def _collect_contact_relationships(contact_ids: Iterable[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    conditions = []
    params: list[Any] = []
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

    relationships_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
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


# ---------------------------------------------------------------------------
# Relationship Type Mappings
# ---------------------------------------------------------------------------


def get_relationship_type_mappings() -> dict[str, list[str]]:
    """
    Get a mapping of relationship types to their related/equivalent types.

    This is useful for resolving generic terms like "my daughter" to actual
    relationship types in the database, which might be stored as "child".

    Returns:
        Dict mapping relationship terms to lists of related database types

    Examples:
        >>> mappings = get_relationship_type_mappings()
        >>> mappings["daughter"]
        ["child", "daughter"]
        >>> mappings["wife"]
        ["spouse", "partner", "wife"]
    """
    return {
        # Family - children
        "daughter": ["child", "daughter"],
        "son": ["child", "son"],
        "child": ["child", "daughter", "son"],

        # Family - spouse/partner
        "wife": ["spouse", "partner", "wife"],
        "husband": ["spouse", "partner", "husband"],
        "spouse": ["spouse", "partner", "wife", "husband"],
        "partner": ["partner", "spouse"],

        # Family - parents
        "mother": ["parent", "mother"],
        "father": ["parent", "father"],
        "parent": ["parent", "mother", "father"],
        "mom": ["parent", "mother"],
        "dad": ["parent", "father"],

        # Family - siblings
        "brother": ["sibling", "brother"],
        "sister": ["sibling", "sister"],
        "sibling": ["sibling", "brother", "sister"],

        # Family - extended
        "grandmother": ["grandparent", "grandmother"],
        "grandfather": ["grandparent", "grandfather"],
        "grandparent": ["grandparent", "grandmother", "grandfather"],
        "grandma": ["grandparent", "grandmother"],
        "grandpa": ["grandparent", "grandfather"],
        "granddaughter": ["grandchild", "granddaughter"],
        "grandson": ["grandchild", "grandson"],
        "grandchild": ["grandchild", "granddaughter", "grandson"],
        "uncle": ["uncle", "aunt"],  # Sometimes people say uncle generically
        "aunt": ["aunt", "uncle"],
        "nephew": ["nephew", "niece"],
        "niece": ["niece", "nephew"],
        "cousin": ["cousin"],

        # Family - step relationships
        "stepfather": ["stepfather", "stepparent"],
        "stepmother": ["stepmother", "stepparent"],
        "stepparent": ["stepfather", "stepmother", "stepparent"],
        "stepson": ["stepson", "stepchild"],
        "stepdaughter": ["stepdaughter", "stepchild"],
        "stepchild": ["stepson", "stepdaughter", "stepchild"],
        "stepbrother": ["stepbrother", "stepsibling"],
        "stepsister": ["stepsister", "stepsibling"],
        "stepsibling": ["stepbrother", "stepsister", "stepsibling"],

        # Family - in-laws
        "father-in-law": ["father-in-law", "parent-in-law"],
        "mother-in-law": ["mother-in-law", "parent-in-law"],
        "parent-in-law": ["father-in-law", "mother-in-law", "parent-in-law"],
        "son-in-law": ["son-in-law", "child-in-law"],
        "daughter-in-law": ["daughter-in-law", "child-in-law"],
        "child-in-law": ["son-in-law", "daughter-in-law", "child-in-law"],
        "brother-in-law": ["brother-in-law", "sibling-in-law"],
        "sister-in-law": ["sister-in-law", "sibling-in-law"],
        "sibling-in-law": ["brother-in-law", "sister-in-law", "sibling-in-law"],

        # Professional - medical
        "doctor": ["doctor", "physician", "dr", "dr."],
        "physician": ["doctor", "physician"],
        "dentist": ["dentist"],
        "orthodontist": ["orthodontist", "dentist"],
        "nurse": ["nurse"],
        "pharmacist": ["pharmacist"],
        "optometrist": ["optometrist"],
        "psychiatrist": ["psychiatrist", "therapist"],
        "therapist": ["therapist", "psychiatrist"],

        # Professional - legal/financial
        "lawyer": ["lawyer", "attorney"],
        "attorney": ["lawyer", "attorney"],
        "accountant": ["accountant"],

        # Professional - work
        "colleague": ["colleague", "co-worker", "coworker"],
        "coworker": ["colleague", "co-worker", "coworker"],
        "co-worker": ["colleague", "co-worker", "coworker"],
        "manager": ["manager", "boss", "supervisor"],
        "boss": ["manager", "boss", "supervisor"],
        "supervisor": ["manager", "boss", "supervisor"],
        "employee": ["employee"],
        "client": ["client", "customer"],
        "customer": ["client", "customer"],
        "vendor": ["vendor", "supplier"],
        "supplier": ["vendor", "supplier"],

        # Professional - education
        "teacher": ["teacher", "instructor"],
        "professor": ["professor", "instructor"],
        "instructor": ["teacher", "professor", "instructor"],
        "tutor": ["tutor"],
        "mentor": ["mentor"],
        "student": ["student"],
        "classmate": ["classmate"],

        # Professional - service providers
        "mechanic": ["mechanic"],
        "plumber": ["plumber"],
        "electrician": ["electrician"],
        "contractor": ["contractor"],
        "hairdresser": ["hairdresser", "barber", "stylist"],
        "barber": ["barber", "hairdresser"],
        "stylist": ["stylist", "hairdresser"],

        # Caregivers
        "babysitter": ["babysitter", "nanny"],
        "nanny": ["nanny", "babysitter"],
        "caregiver": ["caregiver", "aide"],
        "aide": ["aide", "caregiver"],

        # Social
        "friend": ["friend"],
        "neighbor": ["neighbor"],
        "acquaintance": ["acquaintance"],
        "roommate": ["roommate"],
    }


def find_related_types(relationship_type: str) -> list[str]:
    """
    Find all related relationship types for a given type.

    This helps resolve generic relationship terms to their database equivalents.
    For example, "daughter" might be stored as "child" in the database.

    Args:
        relationship_type: The relationship type to look up (e.g., "daughter", "wife")

    Returns:
        List of related types to try, with the original type included as fallback

    Examples:
        >>> find_related_types("daughter")
        ["child", "daughter"]
        >>> find_related_types("wife")
        ["spouse", "partner", "wife"]
        >>> find_related_types("unknown_type")
        ["unknown_type"]
    """
    mappings = get_relationship_type_mappings()
    cleaned = relationship_type.lower().strip()
    return mappings.get(cleaned, [cleaned])
