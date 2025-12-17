from __future__ import annotations

import json
import os
import re
from datetime import datetime
from itertools import combinations
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple
from uuid import uuid4

import contacts as contacts_service
from db import fetch_events, get_conn
from embeddings import embed_text
from schemas import (
    ContactRelationshipIn,
    EventIn,
    ExternalEventPayload,
    MeetingIn,
    TodoIn,
)
from tags_manager import (
    _merge_tag_lists,
    _normalize_strings,
    _suggest_event_tags,
)


MAX_EVENT_EMBED_CHARS = 6000

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


def _format_external_event_id(external_type: str, external_id: str) -> str:
    if not external_type or not external_id:
        raise ValueError("externalType and externalId are required")
    normalized_type = external_type.strip().lower()
    normalized_id = external_id.strip()
    if not normalized_type:
        raise ValueError("externalType cannot be blank")
    if not normalized_id:
        raise ValueError("externalId cannot be blank")
    if normalized_type not in {"google"}:
        raise ValueError(f"Unsupported externalType: {external_type}")
    return f"{normalized_type}:{normalized_id}"


def _load_current_user_from_env() -> Optional[dict]:
    current_user_info = os.environ.get("CURRENT_USER_INFO")
    if not current_user_info:
        return None
    try:
        return json.loads(current_user_info)
    except Exception:
        return None


def _resolve_attendee_contacts(
    attendee_emails: Sequence[str],
    *,
    contact_cache: Dict[str, Tuple[Optional[str], bool]],
    current_user: Optional[dict],
) -> Tuple[List[str], Dict[str, List[str]]]:
    contact_ids: List[str] = []
    new_contacts_by_domain: Dict[str, List[str]] = {}

    for email in attendee_emails:
        normalized = contacts_service.normalize_email(email)
        created_now = False
        contact_id: Optional[str] = None
        if normalized and normalized in contact_cache:
            contact_id, _ = contact_cache[normalized]
        else:
            contact_id, created_now = contacts_service.ensure_contact_for_email(email)
            if normalized:
                contact_cache[normalized] = (contact_id, created_now)
        if contact_id:
            contact_ids.append(contact_id)
            if created_now and normalized and "@" in normalized:
                domain = normalized.split("@", 1)[1]
                new_contacts_by_domain.setdefault(domain, []).append(contact_id)

    unique_contacts = list(dict.fromkeys(contact_ids))

    if current_user:
        current_email = current_user.get("email")
        if current_email:
            normalized_current = contacts_service.normalize_email(current_email)
            if normalized_current and normalized_current not in contact_cache:
                contact_id, created_now = contacts_service.ensure_contact_for_email(current_email)
                if normalized_current:
                    contact_cache[normalized_current] = (contact_id, created_now)
                if contact_id and contact_id not in unique_contacts:
                    unique_contacts.append(contact_id)

    return unique_contacts, new_contacts_by_domain


def _create_coworker_relationships(new_contacts_by_domain: Dict[str, List[str]]) -> None:
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
            contacts_service.upsert_contact_relationship(rel)


def _extract_attendee_emails_from_event(event: EventIn) -> List[str]:
    def _from_value(value: Any) -> Optional[str]:
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return None
            if "<" in candidate and ">" in candidate:
                match = re.search(r"[\w\.\+-]+@[\w\.-]+\.[\w\.-]+", candidate)
                if match:
                    candidate = match.group(0)
            if "@" not in candidate:
                return None
            return candidate
        if isinstance(value, dict):
            for key in ("email", "emailAddress", "address"):
                nested = value.get(key)
                email = _from_value(nested)
                if email:
                    return email
        return None

    emails: List[str] = []
    raw_payload = event.raw if isinstance(event.raw, dict) else {}
    raw_attendees = raw_payload.get("attendees") if raw_payload else None
    if isinstance(raw_attendees, (list, tuple)):
        for attendee in raw_attendees:
            email = _from_value(attendee)
            if email:
                emails.append(email)

    fallback_people = event.people or []
    for person in fallback_people:
        if isinstance(person, str) and "@" in person:
            trimmed = person.strip()
            if trimmed:
                emails.append(trimmed)

    cleaned: List[str] = []
    seen = set()
    for email in emails:
        normalized = email.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned


def ingest_external_event(payload: ExternalEventPayload) -> str:
    event = payload.event
    external_identifier = _format_external_event_id(payload.external_type, payload.event.id)
    existing_id = _get_event_id_by_external_id(external_identifier)

    normalized_event_id = ""
    if existing_id:
        normalized_event_id = existing_id
    if not normalized_event_id:
        normalized_event_id = f"{external_identifier}:{uuid4().hex[:8]}"

    event.id = normalized_event_id
    event.external_id = external_identifier

    contact_cache: Dict[str, Tuple[Optional[str], bool]] = {}
    current_user = _load_current_user_from_env()
    attendee_emails = _extract_attendee_emails_from_event(event)
    unique_contacts, new_contacts_by_domain = _resolve_attendee_contacts(
        attendee_emails,
        contact_cache=contact_cache,
        current_user=current_user,
    )
    if unique_contacts:
        event.people = unique_contacts
        raw_source = event.raw if isinstance(event.raw, dict) else {}
        raw_payload = dict(raw_source)
        if attendee_emails and "attendees" not in raw_payload:
            raw_payload["attendees"] = attendee_emails
        raw_payload["attendee_contact_ids"] = unique_contacts
        event.raw = raw_payload
    _create_coworker_relationships(new_contacts_by_domain)

    ingest_event(event)
    return normalized_event_id

def ingest_meeting_notes(
    meetings: Sequence[MeetingIn],
    *,
    todo_writer: Optional[Callable[[TodoIn], None]] = None,
) -> List[str]:
    event_ids: List[str] = []
    contact_cache: Dict[str, Tuple[Optional[str], bool]] = {}

    current_user = _load_current_user_from_env()

    user_tokens = _build_user_tokens(current_user)
    for meeting in meetings:
        attendee_emails = meeting.attendees or []
        unique_contacts, new_contacts_by_domain = _resolve_attendee_contacts(
            attendee_emails,
            contact_cache=contact_cache,
            current_user=current_user,
        )
        _create_coworker_relationships(new_contacts_by_domain)

        normalized_meeting_id: Optional[str] = None
        provided_meeting_id = getattr(meeting, "id", None)
        if provided_meeting_id is not None:
            normalized_meeting_id = str(provided_meeting_id).strip() or None

        start_date = meeting.date
        title = meeting.title.strip()
        if not title:
            title = normalized_meeting_id or "Untitled meeting"
        tags = list(dict.fromkeys(meeting.tags or []))
        summary = meeting.content or ""

        event_id: Optional[str] = None
        existing_event = False

        if normalized_meeting_id:
            candidate = f"meeting:{normalized_meeting_id}"
            if _event_exists(candidate):
                event_id = candidate
                existing_event = True

        if not event_id:
            matched = _find_matching_meeting_event(title, start_date, unique_contacts)
            if matched:
                event_id = matched
                existing_event = True

        if not event_id:
            event_id = f"meeting:{meeting.date.strftime('%Y%m%dT%H%M%S')}-{_slugify(title)}-{uuid4().hex[:8]}"

        raw_payload = {
            "content": meeting.content,
            "link": meeting.link,
            "attendees": attendee_emails,
            "attendee_contact_ids": unique_contacts,
            "source": "meeting_ingest",
            "existing_event": existing_event,
        }

        if normalized_meeting_id:
            raw_payload["external_meeting_id"] = normalized_meeting_id

        event = EventIn(
            id=event_id,
            startDate=start_date,
            people=unique_contacts,
            tags=tags,
            types=["meeting"],
            title=title,
            summary=summary,
            raw=raw_payload,
        )

        ingest_event(event)
        event_ids.append(event_id)

        if not todo_writer or not user_tokens:
            continue
        existing_todo_signatures = _get_existing_todo_signatures(event_id)
        steps = _extract_next_steps(meeting.content)
        for idx, step in enumerate(steps):
            step_lower = step.lower()
            if not any(token in step_lower for token in user_tokens):
                continue
            normalized_step = _normalize_todo_description(step)
            if not normalized_step or normalized_step in existing_todo_signatures:
                continue
            existing_todo_signatures.add(normalized_step)
            todo = TodoIn(
                todo_id=f"todo:{event_id}:{uuid4().hex[:8]}",
                description=step,
                status="pending",
                contact_ids=[],
                event_ids=[event_id],
                place_ids=[],
            )
            todo_writer(todo)

    return event_ids


def get_meeting(meeting_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              e.id,
              e.start_date,
              e.end_date,
              e.people,
              e.tags,
              e.types,
              e.title,
              e.summary,
              e.external_id,
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

        start_value = row.get("start_date")
        end_value = row.get("end_date")
        place_id = row.get("place_id")

        return {
            "id": row["id"],
            "start_date": start_value.isoformat() if start_value else None,
            "end_date": end_value.isoformat() if end_value else None,
            "title": row.get("title"),
            "summary": row.get("summary"),
            "people": row.get("people") or [],
            "tags": row.get("tags") or [],
            "types": row.get("types") or [],
            "external_id": row.get("external_id"),
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


def normalize_event_types(types: Optional[Sequence[str]]) -> List[str]:
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


def ingest_event(event: EventIn) -> None:
    types = normalize_event_types(event.types)
    normalized_tags = _normalize_strings(event.tags)
    title_text = event.title or ""
    summary_text = event.summary or ""
    suggested_tags = _suggest_event_tags(title_text, summary_text, normalized_tags, types=types)
    merged_tags = _merge_tag_lists(normalized_tags, suggested_tags)

    embedding_payload = {**event.dict(), "tags": merged_tags, "types": types}
    emb = _generate_event_embedding(embedding_payload)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO events (
              id,
              start_date,
              end_date,
              place_id,
              people,
              tags,
              types,
              title,
              summary,
              raw,
              external_id,
              what_embed
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO UPDATE
              SET start_date=EXCLUDED.start_date,
                  end_date=EXCLUDED.end_date,
                  place_id=EXCLUDED.place_id,
                  people=EXCLUDED.people,
                  tags=EXCLUDED.tags,
                  types=EXCLUDED.types,
                  title=EXCLUDED.title,
                  summary=EXCLUDED.summary,
                  raw=EXCLUDED.raw,
                  external_id=EXCLUDED.external_id,
                  what_embed=EXCLUDED.what_embed
            """,
            (
                event.id,
                event.start_date,
                event.end_date,
                event.place_id,
                event.people or [],
                merged_tags,
                types,
                event.title or "",
                event.summary or "",
                json.dumps(event.raw or {}),
                event.external_id,
                emb,
            ),
        )
        conn.commit()


def get_events(ids: List[str]) -> List[Dict[str, Any]]:
    rows = fetch_events(ids)
    return [
        {
            "id": r["id"],
            "start_date": r["start_date"].isoformat() if r.get("start_date") else None,
            "end_date": r["end_date"].isoformat() if r.get("end_date") else None,
            "people": r["people"],
            "tags": r["tags"],
            "types": r.get("types", []),
            "title": r.get("title"),
            "summary": r.get("summary"),
            "external_id": r.get("external_id"),
            "place": (
                {
                    "place_id": r["place_id"],
                    "name": r["place_name"],
                    "city": r["city"],
                    "country": r["country"],
                    "lat": r["lat"],
                    "lon": r["lon"],
                }
                if r.get("place_id")
                else None
            ),
        }
        for r in rows
    ]


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


def _normalize_todo_description(text: Optional[str]) -> str:
    if not text:
        return ""
    squashed = " ".join(text.split())
    return squashed.strip().lower()


def _get_existing_todo_signatures(event_id: Optional[str]) -> Set[str]:
    if not event_id:
        return set()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.description
            FROM todos AS t
            INNER JOIN todo_events AS te ON te.todo_id = t.todo_id
            WHERE te.event_id = %s
            """,
            (event_id,),
        )
        rows = cur.fetchall()
    signatures: Set[str] = set()
    for row in rows:
        signature = _normalize_todo_description(row.get("description"))
        if signature:
            signatures.add(signature)
    return signatures


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


def _get_event_id_by_external_id(external_id: Optional[str]) -> Optional[str]:
    if not external_id:
        return None
    normalized = external_id.strip()
    if not normalized:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM events
            WHERE external_id = %s
            LIMIT 1
            """,
            (normalized,),
        )
        row = cur.fetchone()
        return row["id"] if row else None


def _find_matching_meeting_event(
    title: Optional[str],
    start_date: Optional[datetime],
    attendees: Sequence[str],
) -> Optional[str]:
    if not title or not start_date or not attendees:
        return None
    normalized_title = title.strip()
    if not normalized_title:
        return None
    attendee_set = {att for att in attendees if att}
    if not attendee_set:
        return None

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, people
            FROM events
            WHERE title = %s
              AND start_date = %s
              AND types @> ARRAY['meeting']
            """,
            (normalized_title, start_date),
        )
        rows = cur.fetchall()

    for row in rows:
        existing_people = set(row.get("people") or [])
        if existing_people == attendee_set:
            return row["id"]
    return None


def _slugify(text: str) -> str:
    lowered = (text or "").strip().lower()
    if not lowered:
        return "meeting"
    slug = re.sub(r"[^a-z0-9]+", "-", lowered)
    slug = slug.strip("-")
    return slug or "meeting"


def _generate_event_embedding(event: Any) -> Sequence[float]:
    def _get(field: str) -> Any:
        if isinstance(event, dict):
            return event.get(field)
        return getattr(event, field, None)

    segments: List[str] = []

    title = _get("title")
    if isinstance(title, str):
        cleaned_title = title.strip()
        if cleaned_title:
            segments.append(cleaned_title)

    summary = _get("summary") or _get("content")
    if isinstance(summary, str):
        cleaned_summary = summary.strip()
        if cleaned_summary:
            segments.append(cleaned_summary)

    tags = _get("tags")
    if isinstance(tags, (list, tuple)):
        formatted = ", ".join(str(tag).strip() for tag in tags if isinstance(tag, str) and tag.strip())
        if formatted:
            segments.append(f"tags: {formatted}")

    types = _get("types")
    if isinstance(types, (list, tuple)):
        formatted = ", ".join(str(t).strip() for t in types if isinstance(t, str) and t.strip())
        if formatted:
            segments.append(f"types: {formatted}")

    people = _get("people")
    if isinstance(people, (list, tuple)):
        formatted = ", ".join(str(person).strip() for person in people if person)
        if formatted:
            segments.append(f"people: {formatted}")

    place_id = _get("place_id")
    if place_id:
        segments.append(f"place: {place_id}")

    raw = _get("raw")
    if isinstance(raw, (dict, list)):
        try:
            raw_text = json.dumps(raw, ensure_ascii=False)
        except TypeError:
            raw_text = str(raw)
        if raw_text:
            segments.append(raw_text)
    elif isinstance(raw, str):
        cleaned = raw.strip()
        if cleaned:
            segments.append(cleaned)

    if not segments:
        fallback = _get("id") or _get("event_id") or ""
        segments.append(str(fallback or "event"))

    combined = " ".join(segments).strip()
    if not combined:
        combined = str(_get("id") or _get("event_id") or "event")

    embed_source = combined[:MAX_EVENT_EMBED_CHARS]
    if not embed_source:
        embed_source = "event"

    return embed_text(embed_source)
