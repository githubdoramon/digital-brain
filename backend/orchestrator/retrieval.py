from __future__ import annotations

import json

from collections import defaultdict
from datetime import datetime, timedelta
import re
from itertools import combinations
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import uuid4

from dateparser.search import search_dates
from rapidfuzz import process, fuzz

from db import fetch_events, get_conn
from embeddings import embed_text
from schemas import ContactIn, ContactRelationshipIn, EventIn, MeetingIn, TodoIn


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
              tags
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (contact_id) DO UPDATE
              SET display_name = EXCLUDED.display_name,
                  aliases = EXCLUDED.aliases,
                  birthday = EXCLUDED.birthday,
                  emails = EXCLUDED.emails,
                  phones = EXCLUDED.phones,
                  links = EXCLUDED.links,
                  tags = EXCLUDED.tags
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
            ),
        )
        _upsert_contact_relationships(cur, contact.contact_id, getattr(contact, "relationships", []) or [])
        conn.commit()


def list_contacts() -> List[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, birthday, emails, phones, links, tags
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
                "relationships": relationships_map.get(contact_id, []),
            })
        return contacts


def get_contact(contact_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, birthday, emails, phones, links, tags
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
            "relationships": relationships_map.get(contact_id, []),
        }


def get_contact_by_email(email: str) -> Optional[Dict[str, Any]]:
    if not email:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, birthday, emails, phones, links, tags
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
            "relationships": relationships_map.get(contact_id, []),
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
    print(f"Extracting next steps from content: {content}")
    lines = content.splitlines()
    steps: List[str] = []
    in_section = False
    for raw_line in lines:
        print(f"Raw line: {raw_line}")
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
    print(f"Extracted steps: {steps}")
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

        event_id = f"meeting:{meeting.date.strftime('%Y%m%dT%H%M%S')}-{_slugify(meeting.title)}-{uuid4().hex[:8]}"

        raw_payload = {
            "content": meeting.content,
            "link": meeting.link,
            "attendees": attendee_emails,
            "attendee_contact_ids": unique_contacts,
            "source": "meeting_ingest",
        }

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
                print(f"Step {idx}: {step}")
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


def delete_todo(todo_id: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
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


def _collect_todo_links(conn, todo_ids: Sequence[str]) -> Dict[str, Dict[str, List[str]]]:
    if not todo_ids:
        return {}

    link_map: Dict[str, Dict[str, List[str]]] = {todo_id: {"contacts": [], "events": [], "places": []} for todo_id in todo_ids}

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
            SELECT todo_id, event_id
            FROM todo_events
            WHERE todo_id = ANY(%s)
            """,
            (list(todo_ids),),
        )
        for row in cur.fetchall():
            link_map.setdefault(row["todo_id"], {"contacts": [], "events": [], "places": []})["events"].append(row["event_id"])

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
    limit: int = 5,
) -> Dict[str, Any]:
    span = None
    if time_start and time_end:
        try:
            span = (datetime.fromisoformat(time_start), datetime.fromisoformat(time_end))
        except Exception:
            span = None

    vec = vector_search(query, 50)
    bm = bm25_search(query, 50)
    st = structured_candidates(span, list(people or []), list(place_ids or []), 200)

    cand_ids = set(vec) | set(bm) | set(st)
    scored: List[Tuple[str, float]] = []
    for i in cand_ids:
        v = vec.get(i, 0.0)
        b = bm.get(i, 0.0)
        s = st.get(i, 0.0)
        bonus = 0.05 if s > 0 else 0.0
        scored.append((i, 0.6 * v + 0.3 * b + 0.1 * s + bonus))
    scored.sort(key=lambda x: x[1], reverse=True)
    top_ids = [i for i, _ in scored[:limit]]

    rows = fetch_events(top_ids)
    results = [
        {
            "id": r["id"],
            "ts": r["ts"].isoformat(),
            "place": {
                "place_id": r["place_id"],
                "name": r["place_name"],
                "city": r["city"],
                "country": r["country"],
            },
            "people": r["people"],
            "tags": r["tags"],
            "types": r.get("types", []),
            "snippet": make_snippet(r["what_text"]),
        }
        for r in rows
    ]

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
    detailed = get_events([row["id"] for row in search.get("results", [])])
    return {
        "question": question,
        "resolution": resolution,
        "search_results": search.get("results", []),
        "detailed_events": detailed,
    }
