from __future__ import annotations

import json

from collections import defaultdict
from datetime import date, datetime, timedelta
import re
from itertools import combinations
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import uuid4

from dateparser.search import search_dates
from rapidfuzz import process, fuzz

from db import fetch_events, get_conn
from embeddings import embed_text
from schemas import (
    ContactIn,
    ContactRelationshipIn,
    EventIn,
    ExternalPerson,
    MeetingIn,
    TodoIn,
)


def _upsert_contact_relationships(cur, contact_id: str, relationships: Sequence[Any]) -> None:
    if not relationships:
        return

    for rel in relationships:
        relationship_id = getattr(rel, "relationship_id", None) or getattr(rel, "id", None) or f"rel_{contact_id}_{getattr(rel, 'to_contact_id', '')}"
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


# --------------------------- Ingestion helpers ---------------------------
def ingest_contact(contact) -> None:
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
        _upsert_contact_relationships(cur, contact.contact_id, getattr(contact, "relationships", []) or [])
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
            contacts.append({
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
            })
        return contacts


def get_contact(contact_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, birthday, emails, phones, links, tags, external_id
            FROM contacts
            WHERE contact_id = %s
            """,
            (contact_id,)
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
    if not email:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, birthday, emails, phones, links, tags, external_id
            FROM contacts
            WHERE %s = ANY(emails)
            LIMIT 1
            """,
            (email,),
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
            normalized = _normalize_email(email)
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
        if _is_default_external_display(final_display_name, external_id) and new_display_name and not _is_default_external_display(new_display_name, external_id):
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


def _slugify(text: str) -> str:
    lowered = (text or "").strip().lower()
    if not lowered:
        return "meeting"
    slug = re.sub(r"[^a-z0-9]+", "-", lowered)
    slug = slug.strip("-")
    return slug or "meeting"


def _normalize_email(email: str) -> Optional[str]:
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


def _ensure_contact_for_email(email: str) -> Tuple[Optional[str], bool]:
    normalized = _normalize_email(email)
    if not normalized:
        return None, False
    existing = get_contact_by_email(normalized)
    if existing:
        return existing["contact_id"], False

    base_contact_id = _generate_contact_id(normalized)
    contact_id = base_contact_id
    counter = 1
    while get_contact(contact_id):
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


def _extract_next_steps(content: Optional[str]) -> List[str]:
    if not content:
        return []
    lines = content.splitlines()
    steps: List[str] = []
    in_section = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        is_heading = line.startswith("#")
        if is_heading and "next steps" in line.lower():
            in_section = True
            continue
        if in_section and is_heading:
            break
        if in_section:
            if line.startswith(("-", "*")):
                step = line.lstrip("-* ").strip()
                if step:
                    steps.append(step)
            else:
                match = re.match(r"^\d+[\.)]\s*(.+)$", line)
                if match:
                    steps.append(match.group(1).strip())
    return steps


def _build_user_tokens(user: Optional[dict]) -> List[str]:
    if not user:
        return []
    tokens: List[str] = []
    email = user.get("email") if user else None
    if email and "@" in email:
        local = email.split("@", 1)[0]
        if local:
            tokens.append(local.lower())
    name = user.get("name") if user else None
    if name:
        parts = [p.strip().lower() for p in re.split(r"\s+", name) if p.strip()]
        tokens.extend(parts)
    return [token for token in tokens if token]


def _event_exists(event_id: str) -> bool:
    if not event_id:
        return False
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM events
            WHERE id = %s
            LIMIT 1
            """,
            (event_id,),
        )
        return cur.fetchone() is not None


def ingest_meetings(meetings: Sequence[MeetingIn]) -> List[str]:
    event_ids: List[str] = []
    contact_cache: Dict[str, Tuple[Optional[str], bool]] = {}

    import os
    import json

    current_user = None
    current_user_info = os.environ.get("CURRENT_USER_INFO")
    if current_user_info:
        try:
            current_user = json.loads(current_user_info)
        except Exception:
            current_user = None

    user_tokens = _build_user_tokens(current_user)
    for meeting in meetings:
        attendee_emails = meeting.attendees or []
        contact_ids: List[str] = []
        new_contacts_by_domain: Dict[str, List[str]] = {}
        for email in attendee_emails:
            normalized = _normalize_email(email)
            created_now = False
            if normalized and normalized in contact_cache:
                cid, _ = contact_cache[normalized]
            else:
                cid, created_now = _ensure_contact_for_email(email)
                if normalized:
                    contact_cache[normalized] = (cid, created_now)
            if cid:
                contact_ids.append(cid)
                if created_now and normalized and "@" in normalized:
                    domain = normalized.split("@", 1)[1]
                    new_contacts_by_domain.setdefault(domain, []).append(cid)
        unique_contacts = list(dict.fromkeys(contact_ids))

        # if current user is not on the contact list yet, add them
        if current_user:
            current_email = current_user.get("email")
            if current_email:
                normalized_current = _normalize_email(current_email)
                if normalized_current and normalized_current not in contact_cache:
                    cid, created_now = _ensure_contact_for_email(current_email)
                    if normalized_current:
                        contact_cache[normalized_current] = (cid, created_now)
                    if cid and cid not in unique_contacts:
                        unique_contacts.append(cid)

        for domain, ids in new_contacts_by_domain.items():
            if len(ids) < 2:
                continue
            seen_pairs = set()
            for a, b in combinations(sorted(set(ids)), 2):
                pair_key = (a, b)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                relationship_id = f"rel:coworker:{domain}:{a}:{b}"
                rel = ContactRelationshipIn(
                    relationship_id=relationship_id,
                    from_contact_id=a,
                    to_contact_id=b,
                    relationship_type="Co-worker",
                    reciprocal_type="Co-worker",
                )
                upsert_contact_relationship(rel)

        normalized_meeting_id: Optional[str] = None
        provided_meeting_id = getattr(meeting, "id", None)
        if provided_meeting_id is not None:
            normalized_meeting_id = str(provided_meeting_id).strip()
            if not normalized_meeting_id:
                normalized_meeting_id = None

        if normalized_meeting_id:
            event_id = f"meeting:{normalized_meeting_id}"
            existing_event = _event_exists(event_id)
        else:
            event_id = f"meeting:{meeting.date.strftime('%Y%m%dT%H%M%S')}-{_slugify(meeting.title)}-{uuid4().hex[:8]}"
            existing_event = False

        raw_payload = {
            "content": meeting.content,
            "link": meeting.link,
            "attendees": attendee_emails,
            "attendee_contact_ids": unique_contacts,
            "source": "meeting_ingest",
        }

        if normalized_meeting_id:
            raw_payload["external_meeting_id"] = normalized_meeting_id
            raw_payload["existing_event"] = existing_event

        event = EventIn(
            id=event_id,
            ts=meeting.date,
            people=unique_contacts,
            tags=list(dict.fromkeys(meeting.tags or [])),
            types=["meeting"],
            what_text=meeting.title,
            raw=raw_payload,
        )

        ingest_event(event)
        event_ids.append(event_id)

        if user_tokens:
            steps = _extract_next_steps(meeting.content)
            for idx, step in enumerate(steps):
                step_lower = step.lower()
                if not any(token in step_lower for token in user_tokens):
                    continue
                todo = TodoIn(
                    todo_id=f"todo:{event_id}:{uuid4().hex[:8]}",
                    description=step,
                    status="pending",
                    contact_ids=[],
                    event_ids=[event_id],
                    place_ids=[],
                )
                ingest_todo(todo)

    return event_ids


def delete_contact(contact_id: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM contacts
            WHERE contact_id = %s
            """,
            (contact_id,)
        )
        deleted = cur.rowcount > 0
        conn.commit()
        return deleted


def upsert_contact_relationship(rel) -> None:
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


def list_contact_relationships(contact_id: str) -> List[Dict[str, Any]]:
    relationships_map = _collect_contact_relationships([contact_id])
    return relationships_map.get(contact_id, [])


def delete_contact_relationship(relationship_id: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM contact_relationships
            WHERE relationship_id = %s
            """,
            (relationship_id,),
        )
        deleted = cur.rowcount > 0
        conn.commit()
        return deleted


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


def ingest_todo(todo) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO todos (
              todo_id,
              description,
              status,
              due_date
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (todo_id) DO UPDATE
              SET description = EXCLUDED.description,
                  status = EXCLUDED.status,
                  due_date = EXCLUDED.due_date,
                  updated_at = NOW()
            """,
            (
                todo.todo_id,
                todo.description,
                (todo.status or "pending").strip() or "pending",
                todo.due_date,
            ),
        )

        _replace_todo_links(
            cur,
            todo.todo_id,
            todo.contact_ids or [],
            todo.event_ids or [],
            todo.place_ids or [],
        )

        conn.commit()


def list_todos() -> List[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT todo_id, description, status, due_date, created_at, updated_at
            FROM todos
            ORDER BY
              CASE WHEN due_date IS NULL THEN 1 ELSE 0 END,
              due_date ASC NULLS LAST,
              created_at DESC
            """
        )
        rows = cur.fetchall()

        todo_ids = [row["todo_id"] for row in rows]
        link_map = _collect_todo_links(conn, todo_ids)

        todos: List[Dict[str, Any]] = []
        for row in rows:
            todo_id = row["todo_id"]
            links = link_map.get(todo_id, {})
            todos.append(
                {
                    "todo_id": todo_id,
                    "description": row["description"],
                    "status": row["status"],
                    "due_date": row["due_date"].isoformat() if row["due_date"] else None,
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                    "contacts": links.get("contacts", []),
                    "events": links.get("events", []),
                    "places": links.get("places", []),
                }
            )
        return todos


def get_todo(todo_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT todo_id, description, status, due_date, created_at, updated_at
            FROM todos
            WHERE todo_id = %s
            """,
            (todo_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

        link_map = _collect_todo_links(conn, [todo_id])
        links = link_map.get(todo_id, {})

        return {
            "todo_id": row["todo_id"],
            "description": row["description"],
            "status": row["status"],
            "due_date": row["due_date"].isoformat() if row["due_date"] else None,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            "contacts": links.get("contacts", []),
            "events": links.get("events", []),
            "places": links.get("places", []),
        }


def get_meeting(meeting_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              e.id,
              e.ts,
              e.people,
              e.tags,
              e.types,
              e.what_text,
              e.raw,
              e.place_id,
              p.name AS place_name,
              p.city,
              p.country,
              p.lat,
              p.lon
            FROM events AS e
            LEFT JOIN places AS p ON p.place_id = e.place_id
            WHERE e.id = %s
            """,
            (meeting_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

        raw_data = row.get("raw") or {}
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except json.JSONDecodeError:
                raw_data = {"content": raw_data}

        ts_value = row.get("ts")
        place_id = row.get("place_id")

        return {
            "id": row["id"],
            "ts": ts_value.isoformat() if ts_value else None,
            "title": row.get("what_text"),
            "people": row.get("people") or [],
            "tags": row.get("tags") or [],
            "types": row.get("types") or [],
            "raw": raw_data,
            "place": (
                {
                    "place_id": place_id,
                    "name": row.get("place_name"),
                    "city": row.get("city"),
                    "country": row.get("country"),
                    "lat": row.get("lat"),
                    "lon": row.get("lon"),
                }
                if place_id
                else None
            ),
        }


def delete_todo(todo_id: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM todo_contacts WHERE todo_id = %s", (todo_id,))
        cur.execute("DELETE FROM todo_events WHERE todo_id = %s", (todo_id,))
        cur.execute("DELETE FROM todo_places WHERE todo_id = %s", (todo_id,))
        cur.execute(
            """
            DELETE FROM todos
            WHERE todo_id = %s
            """,
            (todo_id,),
        )
        deleted = cur.rowcount > 0
        conn.commit()
        return deleted


def _replace_todo_links(
    cur,
    todo_id: str,
    contact_ids: Sequence[str],
    event_ids: Sequence[str],
    place_ids: Sequence[str],
) -> None:
    cur.execute("DELETE FROM todo_contacts WHERE todo_id = %s", (todo_id,))
    cur.execute("DELETE FROM todo_events WHERE todo_id = %s", (todo_id,))
    cur.execute("DELETE FROM todo_places WHERE todo_id = %s", (todo_id,))

    for contact_id in contact_ids:
        if contact_id:
            cur.execute(
                "INSERT INTO todo_contacts (todo_id, contact_id) VALUES (%s, %s)",
                (todo_id, contact_id),
            )

    for event_id in event_ids:
        if event_id:
            cur.execute(
                "INSERT INTO todo_events (todo_id, event_id) VALUES (%s, %s)",
                (todo_id, event_id),
            )

    for place_id in place_ids:
        if place_id:
            cur.execute(
                "INSERT INTO todo_places (todo_id, place_id) VALUES (%s, %s)",
                (todo_id, place_id),
            )


def _collect_todo_links(conn, todo_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    if not todo_ids:
        return {}

    link_map: Dict[str, Dict[str, Any]] = {
        todo_id: {"contacts": [], "events": [], "places": []} for todo_id in todo_ids
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT todo_id, contact_id
            FROM todo_contacts
            WHERE todo_id = ANY(%s)
            """,
            (list(todo_ids),),
        )
        for row in cur.fetchall():
            link_map.setdefault(row["todo_id"], {"contacts": [], "events": [], "places": []})["contacts"].append(row["contact_id"])

        cur.execute(
            """
            SELECT
              te.todo_id,
              te.event_id,
              ev.what_text,
              ev.ts
            FROM todo_events AS te
            LEFT JOIN events AS ev ON ev.id = te.event_id
            WHERE te.todo_id = ANY(%s)
            ORDER BY ev.ts NULLS LAST, te.event_id
            """,
            (list(todo_ids),),
        )
        for row in cur.fetchall():
            events = link_map.setdefault(row["todo_id"], {"contacts": [], "events": [], "places": []})["events"]
            event_id = row["event_id"]
            event_detail: Dict[str, Any] = {"id": event_id}
            title = row.get("what_text") or None
            if title:
                event_detail["title"] = title
            else:
                event_detail["title"] = event_id
            ts = row.get("ts")
            if ts:
                event_detail["ts"] = ts.isoformat()
            events.append(event_detail)

        cur.execute(
            """
            SELECT todo_id, place_id
            FROM todo_places
            WHERE todo_id = ANY(%s)
            """,
            (list(todo_ids),),
        )
        for row in cur.fetchall():
            link_map.setdefault(row["todo_id"], {"contacts": [], "events": [], "places": []})["places"].append(row["place_id"])

    return link_map


def ingest_place(place) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO places (place_id, name, city, country, lat, lon, geohash)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (place_id) DO UPDATE
              SET name=EXCLUDED.name, city=EXCLUDED.city, country=EXCLUDED.country,
                  lat=EXCLUDED.lat, lon=EXCLUDED.lon, geohash=EXCLUDED.geohash
            """,
            (place.place_id, place.name, place.city, place.country, place.lat, place.lon, place.geohash),
        )
        conn.commit()


__all__ = ["normalize_event_types"]


EVENT_TYPE_CHOICES = {
    "generic",
    "meeting",
    "communication",
    "task",
    "creation",
    "consumption",
    "travel",
    "personal",
    "system",
    "financial",
    "observation",
    "interaction",
    "education",
    "celebration",
    "purchase",
    "health",
}


def normalize_event_types(types: Optional[Sequence[str]]) -> List[str]:
    """Convert the provided event types into canonical, unique values."""
    if not types:
        return ["generic"]
    normalized: List[str] = []
    for value in types:
        if not isinstance(value, str):
            continue
        cleaned = value.strip().lower()
        if cleaned in EVENT_TYPE_CHOICES and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized or ["generic"]


def ingest_event(event) -> None:
    emb = embed_text(event.what_text or "")
    types = normalize_event_types(event.types)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO events (id, ts, place_id, people, tags, types, what_text, raw, what_embed)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO UPDATE
              SET ts=EXCLUDED.ts,
                  place_id=EXCLUDED.place_id,
                  people=EXCLUDED.people,
                  tags=EXCLUDED.tags,
                  types=EXCLUDED.types,
                  what_text=EXCLUDED.what_text,
                  raw=EXCLUDED.raw,
                  what_embed=EXCLUDED.what_embed
            """,
            (
                event.id,
                event.ts,
                event.place_id,
                event.people or [],
                event.tags or [],
                types,
                event.what_text or "",
                json.dumps(event.raw or {}),
                emb,
            ),
        )
        conn.commit()


# --------------------------- Resolution helpers ---------------------------
def resolve_query(text: str, need_contacts: bool = True, need_places: bool = True) -> Dict[str, Any]:
    q = (text or "").strip()
    people = resolve_entities(q, "contacts", "contact_id", "display_name", "aliases") if need_contacts else []
    places = resolve_entities(q, "places", "place_id", "name") if need_places else []
    span = parse_timespan_text(q)
    return {
        "people": people,
        "places": places,
        "timespan": [span[0].isoformat(), span[1].isoformat()] if span else None,
    }


def parse_timespan_text(q: str) -> Optional[Tuple[datetime, datetime]]:
    found = search_dates(q, settings={"RETURN_AS_TIMEZONE_AWARE": True})
    if not found:
        return None
    dates = [d[1] for d in found]
    if len(dates) == 1:
        dt = dates[0]
        return (dt - timedelta(days=7), dt + timedelta(days=7))
    start, end = min(dates), max(dates)
    return (start, end)


def resolve_entities(
    q: str,
    table: str,
    key_col: str,
    label_col: str,
    alias_col: Optional[str] = None,
    limit: int = 3,
) -> List[str]:
    with get_conn() as conn, conn.cursor() as cur:
        if alias_col:
            cur.execute(f"SELECT {key_col} AS id, {label_col} AS label, {alias_col} AS aliases FROM {table}")
        else:
            cur.execute(f"SELECT {key_col} AS id, {label_col} AS label FROM {table}")
        rows = cur.fetchall()
    choices: List[Tuple[str, str]] = []
    for r in rows:
        choices.append((r["id"], r["label"]))
        if alias_col and r.get("aliases"):
            for a in r["aliases"]:
                choices.append((r["id"], a))
    if not choices:
        return []
    labels = [c[1] for c in choices]
    matches = process.extract(q, labels, scorer=fuzz.WRatio, limit=limit)
    out_ids = {choices[idx][0] for label, score, idx in matches if score >= 85}
    return list(out_ids)


# --------------------------- Search helpers ---------------------------
def search_memories(
    query: str,
    people: Optional[Sequence[str]] = None,
    place_ids: Optional[Sequence[str]] = None,
    time_start: Optional[str] = None,
    time_end: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    span = None
    if time_start and time_end:
        try:
            span = (datetime.fromisoformat(time_start), datetime.fromisoformat(time_end))
        except Exception:
            span = None

    normalized_query = (query or "").strip()

    vec_events = vector_search(normalized_query or "", 50)
    bm_events = bm25_search(normalized_query, 50) if normalized_query else {}
    st_events = structured_candidates(span, list(people or []), list(place_ids or []), 200)

    vec_docs = vector_search_documents(normalized_query, 50) if normalized_query else {}
    bm_docs = bm25_search_documents(normalized_query, 50) if normalized_query else {}

    event_ids = set(vec_events) | set(bm_events) | set(st_events)
    event_scores: Dict[str, float] = {}
    for event_id in event_ids:
        v = vec_events.get(event_id, 0.0)
        b = bm_events.get(event_id, 0.0)
        s = st_events.get(event_id, 0.0)
        bonus = 0.05 if s > 0 else 0.0
        score = 0.6 * v + 0.3 * b + 0.1 * s + bonus
        print(f"[retrieval] event_id={event_id} score={score}")
        event_scores[event_id] = score

    doc_ids = set(vec_docs) | set(bm_docs)
    doc_scores: Dict[str, float] = {}
    for doc_id in doc_ids:
        v = vec_docs.get(doc_id, 0.0)
        b = bm_docs.get(doc_id, 0.0)
        score = 0.6 * v + 0.4 * b
        print(f"[retrieval] doc_id={doc_id} score={score}")
        doc_scores[doc_id] = score


    combined: List[Tuple[str, str, float]] = []
    combined.extend((event_id, "event", event_scores[event_id]) for event_id in event_scores)
    combined.extend((doc_id, "document", doc_scores[doc_id]) for doc_id in doc_scores)
    combined.sort(key=lambda item: item[2], reverse=True)

    print(len(combined))
    print(combined)

    if not combined:
        return {"results": []}

    final_limit = max(1, int(limit))
    top_combined = combined[:final_limit]

    event_ids_ordered = [item_id for item_id, kind, _ in top_combined if kind == "event"]
    doc_ids_ordered = [item_id for item_id, kind, _ in top_combined if kind == "document"]

    event_rows = fetch_events(event_ids_ordered) if event_ids_ordered else []
    event_lookup = {row["id"]: row for row in event_rows}

    doc_lookup = fetch_document_summaries(doc_ids_ordered) if doc_ids_ordered else {}

    results: List[Dict[str, Any]] = []
    for item_id, kind, _ in top_combined:
        if kind == "event":
            row = event_lookup.get(item_id)
            if not row:
                continue
            results.append(
                {
                    "id": row["id"],
                    "kind": "event",
                    "ts": row["ts"].isoformat(),
                    "place": {
                        "place_id": row["place_id"],
                        "name": row["place_name"],
                        "city": row["city"],
                        "country": row["country"],
                    },
                    "people": row["people"],
                    "tags": row["tags"],
                    "types": row.get("types", []),
                    "snippet": make_snippet(row["what_text"]),
                }
            )
        else:
            doc = doc_lookup.get(item_id)
            if not doc:
                continue
            results.append(
                {
                    "id": doc["document_id"],
                    "kind": "document",
                    "title": doc.get("title"),
                    "description": doc.get("description"),
                    "tags": doc.get("tags", []),
                    "document_date": _isoformat(doc.get("document_date")),
                    "created_at": _isoformat(doc.get("created_at")),
                    "updated_at": _isoformat(doc.get("updated_at")),
                    "download_url": doc.get("download_url"),
                    "file_name": doc.get("file_name"),
                    "file_mime": doc.get("file_mime"),
                    "file_size": doc.get("file_size"),
                    "snippet": doc.get("snippet", ""),
                }
            )

    return {"results": results}


def vector_search(query: str, k: int = 50):
    qvec = embed_text(query)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, 1 - (what_embed <=> %s::vector) AS vscore
            FROM events
            ORDER BY what_embed <=> %s::vector
            LIMIT %s
            """,
            (qvec, qvec, k),
        )
        return {r["id"]: float(r["vscore"]) for r in cur.fetchall()}


def vector_search_documents(query: str, k: int = 50) -> Dict[str, float]:
    print(f"[retrieval] vector_search_documents(query={query!r}, k={k})")
    if not query:
        return {}
    qvec = embed_text(query)
    print(f"[retrieval] qvec={qvec}")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT document_id, 1 - (content_embed <=> %s::vector) AS vscore
            FROM documents
            WHERE content_embed IS NOT NULL
            ORDER BY content_embed <=> %s::vector
            LIMIT %s
            """,
            (qvec, qvec, k),
        )
        return {r["document_id"]: float(r["vscore"]) for r in cur.fetchall()}


def bm25_search(query: str, k: int = 50):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, ts_rank_cd(what_tsv, plainto_tsquery('english', %s)) AS bscore
            FROM events
            WHERE what_tsv @@ plainto_tsquery('english', %s)
            ORDER BY bscore DESC
            LIMIT %s
            """,
            (query, query, k),
        )
        return {r["id"]: float(r["bscore"]) for r in cur.fetchall()}


def bm25_search_documents(query: str, k: int = 50) -> Dict[str, float]:
    if not query:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT document_id, ts_rank_cd(content_tsv, plainto_tsquery('english', %s)) AS bscore
            FROM documents
            WHERE content_tsv @@ plainto_tsquery('english', %s)
            ORDER BY bscore DESC
            LIMIT %s
            """,
            (query, query, k),
        )
        return {r["document_id"]: float(r["bscore"]) for r in cur.fetchall()}


def structured_candidates(timespan, people_ids: List[str], place_ids: List[str], k: int = 200):
    clauses = []
    params: List[Any] = []
    if timespan:
        clauses.append("ts BETWEEN %s AND %s")
        params += [timespan[0], timespan[1]]
    if people_ids:
        clauses.append("people && %s")
        params.append(people_ids)
    if place_ids:
        clauses.append("place_id = ANY(%s)")
        params.append(place_ids)
    where = " AND ".join(clauses) if clauses else "TRUE"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, 1.0 AS sscore
            FROM events
            WHERE {where}
            ORDER BY ts DESC
            LIMIT %s
            """,
            (*params, k),
        )
        return {r["id"]: float(r["sscore"]) for r in cur.fetchall()}


def make_snippet(text: Optional[str], length: int = 160) -> str:
    if not text:
        return ""
    t = " ".join(text.split())
    return (t[:length] + "…") if len(t) > length else t


def fetch_document_summaries(document_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    if not document_ids:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                document_id,
                title,
                tags,
                description,
                file_name,
                file_mime,
                file_size,
                document_date,
                created_at,
                updated_at,
                content
            FROM documents
            WHERE document_id = ANY(%s)
            """,
            (list(document_ids),),
        )
        rows = cur.fetchall()

    summaries: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        snippet_source = row.get("description") or row.get("content") or ""
        summaries[row["document_id"]] = {
            "document_id": row["document_id"],
            "title": row.get("title"),
            "tags": row.get("tags") or [],
            "description": row.get("description"),
            "document_date": row.get("document_date"),
            "file_name": row.get("file_name"),
            "file_mime": row.get("file_mime"),
            "file_size": row.get("file_size"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "snippet": make_snippet(snippet_source, length=200),
            "download_url": f"/documents/{row['document_id']}/download",
        }
    return summaries


def _isoformat(value: Optional[Any]) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return None


# --------------------------- Fetch helpers ---------------------------
def get_events(ids: List[str]) -> List[Dict[str, Any]]:
    rows = fetch_events(ids)
    return [
        {
            "id": r["id"],
            "ts": r["ts"].isoformat(),
            "people": r["people"],
            "tags": r["tags"],
            "types": r.get("types", []),
            "what_text": r["what_text"],
            "place": {
                "place_id": r["place_id"],
                "name": r["place_name"],
                "city": r["city"],
                "country": r["country"],
                "lat": r["lat"],
                "lon": r["lon"],
            },
        }
        for r in rows
    ]


# --------------------------- Pipeline ---------------------------
def run_pipeline(question: str, search_limit: int = 3) -> Dict[str, Any]:
    resolution = resolve_query(question)
    timespan = resolution.get("timespan") or [None, None]
    search = search_memories(
        query=question,
        people=resolution.get("people"),
        place_ids=resolution.get("places"),
        time_start=timespan[0],
        time_end=timespan[1],
        limit=search_limit,
    )
    results = search.get("results", []) if isinstance(search, dict) else []
    event_ids = [
        row.get("id")
        for row in results
        if isinstance(row, dict) and row.get("id") and row.get("kind", "event") == "event"
    ]
    detailed = get_events(event_ids)
    document_results = [
        row
        for row in results
        if isinstance(row, dict) and row.get("kind") == "document"
    ]
    return {
        "question": question,
        "resolution": resolution,
        "search_results": results,
        "detailed_events": detailed,
        "document_results": document_results,
    }
