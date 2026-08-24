from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from datetime import date, datetime
from enum import Enum
from itertools import combinations
from typing import Any, Callable
from uuid import uuid4

import contacts as contacts_service
import event_photos as event_photos_service
from db import enrich_people, fetch_events, get_conn
from embeddings import embed_text
from observability.logger import get_runtime_logger
from schemas import (
    ContactRelationshipIn,
    EventIn,
    ExternalEventPayload,
    MeetingTranscriptPayload,
    TodoIn,
)
from search_normalization import normalize_search_text
from tags_manager import (
    _merge_tag_lists,
    _normalize_strings,
    _suggest_event_tags,
)

logger = get_runtime_logger(__name__)

MAX_EVENT_EMBED_CHARS = 6000
# GPT-OSS has a 128k token window. 300k transcript chars is a conservative direct-pass cap
# that leaves room for instructions, participant context, and structured JSON output.
MAX_TRANSCRIPT_SUMMARY_INPUT_CHARS = 300_000
MEETING_TRANSCRIPT_SUMMARY_TIMEOUT_SECONDS = 600
SUMMARY_LOG_PREVIEW_CHARS = 3000
ACTION_ITEMS_LOG_PREVIEW_CHARS = 5000

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


class EventMergeMode(str, Enum):
    ADDITIVE = "additive"
    AUTHORITATIVE_EXTERNAL = "authoritative_external"


def _format_external_event_id(external_type: str, external_id: str) -> str:
    if not external_type or not external_id:
        raise ValueError("externalType and externalId are required")
    normalized_type = external_type.strip().lower()
    normalized_id = external_id.strip()
    if not normalized_type:
        raise ValueError("externalType cannot be blank")
    if not normalized_id:
        raise ValueError("externalId cannot be blank")
    if normalized_type not in {"google", "hyprnote"}:
        raise ValueError(f"Unsupported externalType: {external_type}")
    return f"{normalized_type}:{normalized_id}"


def _guess_external_event_id(meeting_id: str | None, meeting_link: str | None) -> str | None:
    if not meeting_id:
        return None
    normalized_id = meeting_id.strip()
    if not normalized_id:
        return None
    if ":" in normalized_id:
        prefix, raw = normalized_id.split(":", 1)
        if prefix.strip().lower() == "google" and raw.strip():
            return f"google:{raw.strip()}"
    link = (meeting_link or "").lower()
    if "google" in link or "@google" in normalized_id.lower():
        try:
            return _format_external_event_id("google", normalized_id)
        except ValueError:
            return None
    return None


def _load_current_user_from_env() -> dict | None:
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
    contact_cache: dict[str, tuple[str | None, bool]],
    current_user: dict | None,
) -> tuple[list[str], dict[str, list[str]]]:
    contact_ids: list[str] = []
    attendee_contacts_by_domain: dict[str, list[str]] = {}

    for email in attendee_emails:
        normalized = contacts_service.normalize_email(email)
        created_now = False
        contact_id: str | None = None
        if normalized and normalized in contact_cache:
            contact_id, _ = contact_cache[normalized]
        else:
            contact_id, created_now = contacts_service.ensure_contact_for_email(email)
            if normalized:
                contact_cache[normalized] = (contact_id, created_now)
        if contact_id:
            contact_ids.append(contact_id)
            if normalized and "@" in normalized:
                domain = normalized.split("@", 1)[1]
                attendee_contacts_by_domain.setdefault(domain, []).append(contact_id)

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
                if contact_id and normalized_current and "@" in normalized_current:
                    domain = normalized_current.split("@", 1)[1]
                    attendee_contacts_by_domain.setdefault(domain, []).append(contact_id)

    return unique_contacts, attendee_contacts_by_domain


def _collect_attendee_emails(*sources: Any) -> list[str]:
    emails: list[str] = []
    seen: set[str] = set()

    def _add_items(value: Any) -> None:
        if value is None:
            return
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, Sequence) or isinstance(items, (bytes, bytearray, str)):
            return
        for item in items:
            email: str | None = None
            if isinstance(item, str):
                email = item
            elif isinstance(item, dict):
                for key in ("email", "mail", "address"):
                    candidate = item.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        email = candidate
                        break
            normalized = contacts_service.normalize_email(email) if email else None
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            emails.append(normalized)

    for source in sources:
        _add_items(source)
    return emails


def _get_existing_relationship_ids(relationship_ids: Sequence[str]) -> set[str]:
    if not relationship_ids:
        return set()

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT relationship_id
            FROM contact_relationships
            WHERE relationship_id = ANY(%s)
            """,
            (list(relationship_ids),),
        )
        rows = [dict(row) for row in cur.fetchall()]
        existing_ids: set[str] = set()
        for row in rows:
            relationship_id = str(row["relationship_id"] or "")
            if relationship_id:
                existing_ids.add(relationship_id)
        return existing_ids


def _create_coworker_relationships(attendee_contacts_by_domain: dict[str, list[str]]) -> None:
    candidate_relationships: list[tuple[str, str, str]] = []
    for domain, ids in attendee_contacts_by_domain.items():
        unique_ids = sorted(set(ids))
        if len(unique_ids) < 2:
            continue
        for a, b in combinations(unique_ids, 2):
            relationship_id = f"rel:coworker:{domain}:{a}:{b}"
            candidate_relationships.append((relationship_id, a, b))

    existing_relationship_ids = _get_existing_relationship_ids(
        [relationship_id for relationship_id, _a, _b in candidate_relationships]
    )
    for relationship_id, a, b in candidate_relationships:
        if relationship_id in existing_relationship_ids:
            continue
        rel = ContactRelationshipIn(
            relationship_id=relationship_id,
            from_contact_id=a,
            to_contact_id=b,
            relationship_type="Co-worker",
            reciprocal_type="Co-worker",
        )
        contacts_service.upsert_contact_relationship(rel)


def ingest_external_event(payload: ExternalEventPayload) -> str:
    event = payload.event
    external_identifier = _format_external_event_id(payload.external_type, payload.event.id)
    existing_id = _get_event_id_by_external_id(external_identifier)

    normalized_event_id = ""
    if existing_id:
        normalized_event_id = existing_id
    if not normalized_event_id:
        matched = _find_matching_meeting_event(event.title, event.start_date)
        if matched:
            normalized_event_id = matched
    if not normalized_event_id:
        normalized_event_id = f"{external_identifier}:{uuid4().hex[:8]}"

    if normalized_event_id and normalized_event_id != event.id:
        event.id = normalized_event_id
    event.external_id = external_identifier

    existing_event = _get_event_by_id(normalized_event_id)
    if existing_event:
        event = _merge_event(existing_event, event, mode=EventMergeMode.AUTHORITATIVE_EXTERNAL)

    raw_payload = event.raw if isinstance(event.raw, dict) else {}
    attendee_emails = _collect_attendee_emails(
        event.attendees_emails,
        raw_payload.get("attendees"),
        raw_payload.get("attendeesEmails"),
        raw_payload.get("attendeeEmails"),
    )
    contact_cache: dict[str, tuple[str | None, bool]] = {}
    current_user = _load_current_user_from_env()
    unique_contacts, attendee_contacts_by_domain = _resolve_attendee_contacts(
        attendee_emails,
        contact_cache=contact_cache,
        current_user=current_user,
    )
    if unique_contacts:
        event.people = unique_contacts
    _create_coworker_relationships(attendee_contacts_by_domain)

    ingest_event(event)
    return normalized_event_id


def ingest_meeting_transcript(
    payload: MeetingTranscriptPayload,
    *,
    current_user: dict,
    todo_writer: Callable[[TodoIn], None] | None = None,
) -> dict[str, Any]:
    current_email = contacts_service.normalize_email(current_user.get("email") or "")
    if not current_email:
        raise ValueError("current_user with email is required")

    meeting = payload.meeting
    title = (meeting.title or "").strip() or "Untitled meeting"
    start_date = meeting.started_at
    end_date = meeting.ended_at
    transcript_text = _format_meeting_transcript(payload)

    external_identifier = _get_transcript_external_identifier(payload)
    event_id = _resolve_meeting_transcript_event_id(
        title=title,
        start_date=start_date,
        external_identifier=external_identifier,
        session_id=payload.session_id,
    )

    contact_cache: dict[str, tuple[str | None, bool]] = {}
    participants = _collect_meeting_transcript_participants(payload)
    unique_contacts, attendee_contacts_by_domain = _resolve_transcript_participant_contacts(
        participants,
        contact_cache=contact_cache,
        current_user=current_user,
    )
    _create_coworker_relationships(attendee_contacts_by_domain)
    logger.info(
        "[meeting_transcript] Contacts resolved upload_id=%s participant_count=%d contact_ids=%s",
        payload.upload_id,
        len(participants),
        unique_contacts,
    )
    people_context = _build_meeting_transcript_people_context(
        participants,
        contact_ids=unique_contacts,
        current_user=current_user,
    )

    summary_result = _generate_meeting_transcript_summary(
        payload,
        transcript_text,
        current_user=current_user,
        people_context=people_context,
    )
    summary = summary_result["summary"]
    action_items = _annotate_action_items_for_current_user(
        summary_result["action_items"],
        current_user=current_user,
        current_user_identifiers=people_context.get("current_user_identifiers") or [],
    )
    summary_result = {**summary_result, "action_items": action_items}
    logger.info(
        "[meeting_transcript] Summary generated upload_id=%s session_id=%s transcript_hash=%s "
        "summary_preview=%s action_items=%s",
        payload.upload_id,
        payload.session_id,
        payload.transcript_hash,
        _truncate_log_text(summary, SUMMARY_LOG_PREVIEW_CHARS),
        _truncate_log_text(json.dumps(action_items, ensure_ascii=False), ACTION_ITEMS_LOG_PREVIEW_CHARS),
    )

    raw_payload = {
        "source": "meeting_transcript_ingest",
        "upload_id": payload.upload_id,
        "session_id": payload.session_id,
        "transcript_hash": payload.transcript_hash,
        "meeting": payload.meeting.model_dump(by_alias=True, mode="json"),
        "participants": [participant.model_dump() for participant in payload.participants],
        "speaker_identities": [identity.model_dump() for identity in payload.speaker_identities],
        "transcript": payload.transcript.model_dump(by_alias=True, mode="json"),
        "transcript_text": transcript_text,
        "people_context": people_context,
        "summary_result": summary_result,
        "action_items": action_items,
        "attendee_contact_ids": unique_contacts,
    }

    event = EventIn(
        id=event_id,
        startDate=start_date,
        endDate=end_date,
        people=unique_contacts,
        types=["meeting"],
        title=title,
        summary=summary,
        raw=raw_payload,
        externalId=external_identifier,
    )

    existing_event = _get_event_by_id(event_id)
    if existing_event:
        event = _merge_event(existing_event, event, mode=EventMergeMode.AUTHORITATIVE_EXTERNAL)
        event.summary = summary

    ingest_event(event)
    created_todo_ids = _create_current_user_todos_from_action_items(
        action_items,
        event_id=event_id,
        current_user=current_user,
        current_user_identifiers=people_context.get("current_user_identifiers") or [],
        current_user_contact_id=(contact_cache.get(current_email) or (None, False))[0],
        todo_writer=todo_writer,
    )
    logger.info(
        "[meeting_transcript] Ingestion complete upload_id=%s event_id=%s created_todo_ids=%s",
        payload.upload_id,
        event_id,
        created_todo_ids,
    )
    return {
        "event_id": event_id,
        "summary": summary,
        "action_items": action_items,
        "created_todo_ids": created_todo_ids,
        "contact_ids": unique_contacts,
    }


def _truncate_log_text(value: str | None, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _get_transcript_external_identifier(payload: MeetingTranscriptPayload) -> str | None:
    provider = (payload.meeting.provider or "").strip().lower()
    original_id = (payload.meeting.original_id or "").strip()
    if not provider or not original_id:
        return None
    try:
        return _format_external_event_id(provider, original_id)
    except ValueError:
        logger.warning(
            "[meeting_transcript] Unsupported external meeting provider=%s; storing without external_id",
            provider,
        )
        return None


def _resolve_meeting_transcript_event_id(
    *,
    title: str,
    start_date: datetime,
    external_identifier: str | None,
    session_id: str | None,
) -> str:
    if external_identifier:
        existing_id = _get_event_id_by_external_id(external_identifier)
        if existing_id:
            return existing_id

    matched = _find_matching_meeting_event(title, start_date)
    if matched:
        return matched

    if external_identifier:
        return f"{external_identifier}:{uuid4().hex[:8]}"

    normalized_session = _slugify(session_id or start_date.strftime("%Y%m%dT%H%M%S"))
    return f"meeting-transcript:{normalized_session}-{_slugify(title)}-{uuid4().hex[:8]}"


def _speaker_label_map(payload: MeetingTranscriptPayload) -> dict[str, str]:
    labels: dict[str, str] = {}
    for identity in payload.speaker_identities:
        speaker_id = str(identity.id or "").strip()
        if not speaker_id:
            continue
        identity_payload = identity.identity if isinstance(identity.identity, dict) else {}
        label = (
            str(identity_payload.get("name") or "").strip()
            or str(identity.label or "").strip()
            or str(identity_payload.get("email") or "").strip()
            or speaker_id
        )
        labels[speaker_id] = label
    return labels


def _format_meeting_transcript(payload: MeetingTranscriptPayload) -> str:
    labels = _speaker_label_map(payload)
    turns: list[dict[str, str]] = []
    for segment in _sorted_transcript_segments(payload.transcript.segments):
        text = " ".join(str(segment.text or "").split()).strip()
        if not text:
            continue
        label = labels.get(str(segment.speaker_id or ""), segment.speaker_id or "Unknown speaker")
        if turns and turns[-1]["label"] == label:
            turns[-1]["text"] = _join_transcript_fragments(turns[-1]["text"], text)
        else:
            turns.append({"label": label, "text": text})
    return "\n".join(f"{turn['label']}: {turn['text']}" for turn in turns).strip()


def _sorted_transcript_segments(segments: Sequence[Any]) -> list[Any]:
    indexed_segments = list(enumerate(segments))

    def sort_key(item: tuple[int, Any]) -> tuple[float, int]:
        index, segment = item
        started_at = getattr(segment, "started_at", None)
        if isinstance(started_at, datetime):
            return started_at.timestamp(), index
        return float(index), index

    return [segment for _index, segment in sorted(indexed_segments, key=sort_key)]


def _join_transcript_fragments(existing: str, incoming: str) -> str:
    existing_text = existing.strip()
    incoming_text = incoming.strip()
    if not existing_text:
        return incoming_text
    if not incoming_text:
        return existing_text
    if existing_text.endswith(("-", "/", "(", "[", "{")):
        return f"{existing_text}{incoming_text}"
    return f"{existing_text} {incoming_text}"


def _collect_meeting_transcript_participants(
    payload: MeetingTranscriptPayload,
) -> list[dict[str, str | None]]:
    participants: list[dict[str, str | None]] = []

    def add_participant(name: str | None, email: str | None, source: str | None) -> None:
        cleaned_name = " ".join(str(name or "").split()).strip() or None
        normalized_email = contacts_service.normalize_email(email or "")
        if not cleaned_name and not normalized_email:
            return
        participants.append(
            {
                "name": cleaned_name,
                "email": normalized_email,
                "source": source,
            }
        )

    for participant in payload.participants:
        add_participant(participant.name, participant.email, participant.source)

    for speaker_identity in payload.speaker_identities:
        identity = speaker_identity.identity if isinstance(speaker_identity.identity, dict) else {}
        if identity.get("kind") not in {"participant", "current_user"}:
            continue
        add_participant(
            identity.get("name") or speaker_identity.label,
            identity.get("email"),
            speaker_identity.source or "speaker_identity",
        )

    deduped: list[dict[str, str | None]] = []
    seen: set[tuple[str, str]] = set()
    for participant in participants:
        key = (
            normalize_search_text(participant.get("name") or ""),
            contacts_service.normalize_email(participant.get("email") or "") or "",
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(participant)
    return deduped


def _resolve_transcript_participant_contacts(
    participants: Sequence[dict[str, str | None]],
    *,
    contact_cache: dict[str, tuple[str | None, bool]],
    current_user: dict,
) -> tuple[list[str], dict[str, list[str]]]:
    current_email = contacts_service.normalize_email(current_user.get("email") or "")
    if not current_email:
        raise ValueError("current_user with email is required")

    contact_ids: list[str] = []
    attendee_contacts_by_domain: dict[str, list[str]] = {}

    for participant in participants:
        email = contacts_service.normalize_email(participant.get("email") or "")
        if not email:
            continue
        display_name = participant.get("name")
        created_now = False
        contact_id: str | None = None
        if email in contact_cache:
            contact_id, _ = contact_cache[email]
        else:
            contact_id, created_now = contacts_service.ensure_contact_for_email(
                email,
                display_name=display_name,
            )
            contact_cache[email] = (contact_id, created_now)
        if contact_id:
            contact_ids.append(contact_id)
            if "@" in email:
                domain = email.split("@", 1)[1]
                attendee_contacts_by_domain.setdefault(domain, []).append(contact_id)

    if current_email not in contact_cache:
        contact_id, created_now = contacts_service.ensure_contact_for_email(
            current_email,
            display_name=current_user.get("name"),
        )
        contact_cache[current_email] = (contact_id, created_now)
        if contact_id:
            contact_ids.append(contact_id)
            if "@" in current_email:
                domain = current_email.split("@", 1)[1]
                attendee_contacts_by_domain.setdefault(domain, []).append(contact_id)

    return list(dict.fromkeys(contact_ids)), attendee_contacts_by_domain


def _build_meeting_transcript_people_context(
    participants: Sequence[dict[str, str | None]],
    *,
    contact_ids: Sequence[str],
    current_user: dict,
) -> dict[str, Any]:
    all_identifiers: set[str] = set()
    current_user_identifiers: set[str] = set()
    contact_entries: list[dict[str, Any]] = []

    def add_terms(target: set[str], *values: Any) -> None:
        for value in values:
            if isinstance(value, (list, tuple, set)):
                add_terms(target, *value)
                continue
            cleaned = " ".join(str(value or "").split()).strip()
            if cleaned:
                target.add(cleaned)

    current_email = contacts_service.normalize_email(current_user.get("email") or "")
    add_terms(current_user_identifiers, current_user.get("name"), current_email)

    for participant in participants:
        add_terms(all_identifiers, participant.get("name"), participant.get("email"))

    for contact_id in contact_ids:
        contact = contacts_service.get_contact(contact_id)
        if not contact:
            continue
        contact_identifiers: set[str] = set()
        add_terms(
            contact_identifiers,
            contact.get("display_name"),
            contact.get("aliases") or [],
            contact.get("emails") or [],
        )
        if not contact_identifiers:
            continue

        all_identifiers.update(contact_identifiers)
        normalized_emails = {
            contacts_service.normalize_email(email)
            for email in contact.get("emails") or []
            if contacts_service.normalize_email(email)
        }
        normalized_names = {normalize_search_text(term) for term in contact_identifiers}
        if current_email in normalized_emails or normalize_search_text(current_user.get("name") or "") in normalized_names:
            current_user_identifiers.update(contact_identifiers)

        contact_entries.append(
            {
                "contact_id": contact_id,
                "identifiers": sorted(contact_identifiers, key=lambda item: item.casefold()),
            }
        )

    all_identifiers.update(current_user_identifiers)
    return {
        "current_user_identifiers": sorted(current_user_identifiers, key=lambda item: item.casefold()),
        "people_identifiers": sorted(all_identifiers, key=lambda item: item.casefold()),
        "contacts": contact_entries,
    }


def _format_identifier_lines(identifiers: Sequence[Any]) -> str:
    return "\n".join(f"- {identifier}" for identifier in identifiers if str(identifier or "").strip())


def _format_people_context_contacts(contacts: Sequence[Any]) -> str:
    lines: list[str] = []
    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        identifiers = ", ".join(str(item) for item in contact.get("identifiers") or [] if item)
        if not identifiers:
            continue
        lines.append(f"- {contact.get('contact_id')}: {identifiers}")
    return "\n".join(lines)


def _generate_meeting_transcript_summary(
    payload: MeetingTranscriptPayload,
    transcript_text: str,
    *,
    current_user: dict,
    people_context: dict[str, Any],
) -> dict[str, Any]:
    if not transcript_text:
        return _fallback_meeting_transcript_summary(payload, transcript_text)

    from llm_helpers import (
        LLMUnavailableError,
        build_json_schema_response_format,
        call_llm_json_agentic,
    )
    from llm_json_schemas import MEETING_TRANSCRIPT_SUMMARY_RESPONSE_SCHEMA

    current_user_identifiers = _format_identifier_lines(
        people_context.get("current_user_identifiers") or []
    )
    people_identifiers = _format_identifier_lines(people_context.get("people_identifiers") or [])
    resolved_contacts = _format_people_context_contacts(people_context.get("contacts") or [])
    authenticated_current_email = contacts_service.normalize_email(current_user.get("email") or "") or "unknown"
    prompt = f"""
Summarize this meeting transcript for a personal memory system and extract action items.

Meeting title: {payload.meeting.title or "Untitled meeting"}
Meeting description: {payload.meeting.description or ""}
Authenticated current user email: {authenticated_current_email}
Current user identifiers (names, aliases, emails):
{current_user_identifiers or "- Unknown"}

All known identifiers for people involved (names, aliases, emails):
{people_identifiers or "- Unknown"}

Resolved contacts:
{resolved_contacts or "- None"}

Transcript:
{transcript_text[:MAX_TRANSCRIPT_SUMMARY_INPUT_CHARS]}

Return valid JSON only, matching the supplied response schema.

Summary rules:
- Include actual discussion topics, important context, decisions, and follow-ups when present.
- Always output content in English, even if original language of transcript is not English.
- If possible, identify who mentioned key points or decisions.
- Do not invent facts.

Action item rules:
- Only include action items that are explicit or strongly implied by a concrete commitment in the transcript.
- Assign action items to the named speaker/participant when the transcript makes the assignee clear.
- Use the current user's canonical name/email from the identifier set when the current user is the assignee.
- Use participant aliases/emails from the identifier sets to disambiguate assignees.
- Use null for unknown assignee_name, assignee_email, due_date, or evidence fields.
- Do not create action items for vague discussion topics or suggestions without ownership.
""".strip()
    response_format = build_json_schema_response_format(
        name="meeting_transcript_summary",
        schema=MEETING_TRANSCRIPT_SUMMARY_RESPONSE_SCHEMA,
    )

    try:
        generated = call_llm_json_agentic(
            prompt,
            system_prompt=(
                "You write accurate meeting summaries and extract only grounded action items. "
                "Return schema-valid JSON. Use your reasoning as needed, then emit the final "
                "JSON object. Do not stop after reasoning without producing the object."
            ),
            use_fast_model=False,
            timeout=MEETING_TRANSCRIPT_SUMMARY_TIMEOUT_SECONDS,
            max_tokens=16_384,
            temperature=0.2,
            reasoning_effort="x-high",
            response_format=response_format,
            max_turns=3,
            result_validator=_is_satisfactory_meeting_summary,
        )
    except (LLMUnavailableError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("[meeting_transcript] LLM summary unavailable after agentic continuation: %s", exc)
        return _fallback_meeting_transcript_summary(payload, transcript_text)

    return _normalize_meeting_summary_result(generated, payload, transcript_text)


def _normalize_meeting_summary_result(
    result: dict[str, Any],
    payload: MeetingTranscriptPayload,
    transcript_text: str,
) -> dict[str, Any]:
    summary = " ".join(str(result.get("summary") or "").split()).strip()
    if not summary:
        return _fallback_meeting_transcript_summary(payload, transcript_text)

    action_items: list[dict[str, str | None]] = []
    raw_action_items = result.get("action_items")
    if isinstance(raw_action_items, list):
        for item in raw_action_items:
            if not isinstance(item, dict):
                continue
            task = " ".join(str(item.get("task") or "").split()).strip()
            if not task:
                continue
            action_items.append(
                {
                    "task": task,
                    "assignee_name": _clean_optional_text(item.get("assignee_name")),
                    "assignee_email": contacts_service.normalize_email(item.get("assignee_email") or ""),
                    "due_date": _clean_optional_text(item.get("due_date")),
                    "evidence": _clean_optional_text(item.get("evidence")),
                }
            )

    return {"summary": summary, "action_items": action_items}


def _is_satisfactory_meeting_summary(result: dict[str, Any]) -> bool:
    summary = " ".join(str(result.get("summary") or "").split()).strip()
    action_items = result.get("action_items")
    if not summary or not isinstance(action_items, list):
        return False
    return all(
        isinstance(item, dict) and " ".join(str(item.get("task") or "").split()).strip()
        for item in action_items
    )


def _create_current_user_todos_from_action_items(
    action_items: Sequence[dict[str, Any]],
    *,
    event_id: str,
    current_user: dict,
    current_user_identifiers: Sequence[Any],
    current_user_contact_id: str | None,
    todo_writer: Callable[[TodoIn], None] | None,
) -> list[str]:
    if not todo_writer:
        return []

    current_email = contacts_service.normalize_email(current_user.get("email") or "")
    current_emails = _current_user_email_set(
        current_user,
        current_user_identifiers=current_user_identifiers,
    )
    current_identifier_terms = {
        normalize_search_text(identifier)
        for identifier in current_user_identifiers
        if normalize_search_text(identifier)
    }
    current_identifier_terms.add(normalize_search_text(current_user.get("name") or ""))
    current_identifier_terms = {term for term in current_identifier_terms if term}
    existing_todo_signatures = _get_existing_todo_signatures(event_id)
    created_todo_ids: list[str] = []

    for action_item in action_items:
        if not _is_current_user_action_item(
            action_item,
            current_emails=current_emails or ({current_email} if current_email else set()),
            current_identifier_terms=current_identifier_terms,
        ):
            continue

        task = _clean_optional_text(action_item.get("task"))
        normalized_task = _normalize_todo_description(task)
        if not task or not normalized_task or normalized_task in existing_todo_signatures:
            continue

        existing_todo_signatures.add(normalized_task)
        todo_id = f"todo:{event_id}:transcript:{uuid4().hex[:8]}"
        todo_writer(
            TodoIn(
                todo_id=todo_id,
                description=task,
                status="pending",
                due_date=_parse_action_item_due_date(action_item.get("due_date")),
                contact_ids=[current_user_contact_id] if current_user_contact_id else [],
                event_ids=[event_id],
                place_ids=[],
            )
        )
        created_todo_ids.append(todo_id)

    return created_todo_ids


def _current_user_email_set(
    current_user: dict,
    *,
    current_user_identifiers: Sequence[Any],
) -> set[str]:
    emails: set[str] = set()
    current_email = contacts_service.normalize_email(current_user.get("email") or "")
    if current_email:
        emails.add(current_email)
    for identifier in current_user_identifiers:
        text = str(identifier or "").strip()
        if "@" not in text:
            continue
        normalized = contacts_service.normalize_email(text)
        if normalized:
            emails.add(normalized)
    return emails


def _annotate_action_items_for_current_user(
    action_items: Sequence[dict[str, Any]],
    *,
    current_user: dict,
    current_user_identifiers: Sequence[Any],
) -> list[dict[str, Any]]:
    current_emails = _current_user_email_set(
        current_user,
        current_user_identifiers=current_user_identifiers,
    )
    current_identifier_terms = {
        normalize_search_text(identifier)
        for identifier in current_user_identifiers
        if normalize_search_text(identifier)
    }
    current_identifier_terms.add(normalize_search_text(current_user.get("name") or ""))
    current_identifier_terms = {term for term in current_identifier_terms if term}

    annotated: list[dict[str, Any]] = []
    for action_item in action_items:
        item = dict(action_item)
        item["belongs_to_current_user"] = _is_current_user_action_item(
            item,
            current_emails=current_emails,
            current_identifier_terms=current_identifier_terms,
        )
        annotated.append(item)
    return annotated


def _is_current_user_action_item(
    action_item: dict[str, Any],
    *,
    current_emails: set[str],
    current_identifier_terms: set[str],
) -> bool:
    assignee_email = contacts_service.normalize_email(action_item.get("assignee_email") or "")
    if assignee_email and assignee_email in current_emails:
        return True

    assignee_name = normalize_search_text(action_item.get("assignee_name") or "")
    if not assignee_name:
        return False
    if assignee_name in {"current user", "me", "myself", "you"}:
        return True
    return assignee_name in current_identifier_terms


def _parse_action_item_due_date(value: Any) -> date | None:
    cleaned = _clean_optional_text(value)
    if not cleaned:
        return None
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        return None


def _clean_optional_text(value: Any) -> str | None:
    cleaned = " ".join(str(value or "").split()).strip()
    return cleaned or None


def _fallback_meeting_transcript_summary(
    payload: MeetingTranscriptPayload,
    transcript_text: str,
) -> dict[str, Any]:
    description = " ".join(str(payload.meeting.description or "").split()).strip()
    excerpt = " ".join(str(transcript_text or "").split()).strip()
    if excerpt:
        if len(excerpt) > 1200:
            excerpt = f"{excerpt[:1197].rstrip()}..."
        return {"summary": f"Transcript excerpt: {excerpt}", "action_items": []}
    if description:
        return {"summary": description, "action_items": []}
    return {"summary": payload.meeting.title or "Meeting transcript received.", "action_items": []}


def get_meeting(meeting_id: str) -> dict[str, Any] | None:
    from db import fetch_event_people

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              e.id,
              e.start_date,
              e.end_date,
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

        people_map = fetch_event_people(cur, [meeting_id])
        people = people_map.get(meeting_id, [])

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
            "people": people,
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


def normalize_event_types(types: Sequence[str] | None) -> list[str]:
    if not types:
        return ["generic"]
    normalized: list[str] = []
    for value in types:
        if not isinstance(value, str):
            continue
        cleaned = value.strip().lower()
        if cleaned in EVENT_TYPE_CHOICES and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized or ["generic"]


def _display_name_from_contact_id(contact_id: str) -> str:
    """Derive a human-readable display name from a contact ID.

    Examples:
        contact:robin-lake-example-com -> Robin Lake
        contact:alice#001               -> Alice
    """
    name = contact_id.removeprefix("contact:")
    # Strip trailing hash disambiguators (e.g. #001)
    if "#" in name:
        name = name.rsplit("#", 1)[0]
    # Strip email-domain suffixes baked into the ID
    for suffix in (
        "-gmail-com",
        "-outlook-com",
        "-yahoo-com",
        "-hotmail-com",
        "-com",
        "-org",
        "-net",
    ):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.replace("-", " ").strip().title() or contact_id


def _ensure_stub_contacts(contact_ids: list[str]) -> None:
    """Auto-create minimal contact records for IDs that don't exist yet."""
    from schemas import ContactIn

    for cid in contact_ids:
        display_name = _display_name_from_contact_id(cid)
        stub = ContactIn(
            contact_id=cid,
            display_name=display_name,
            tags=["autocreated"],
        )
        contacts_service.ingest_contact(stub)
        logger.info("[ingest_event] Auto-created stub contact %s (%s)", cid, display_name)


def ingest_event(event: EventIn) -> None:
    types = normalize_event_types(event.types)
    normalized_tags = _normalize_strings(event.tags)

    embedding_payload = {**event.dict(), "tags": normalized_tags, "types": types}
    emb = _generate_event_embedding(embedding_payload)
    people_ids = list(dict.fromkeys(event.people or []))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO events (
              id,
              start_date,
              end_date,
              place_id,
              tags,
              types,
              title,
              summary,
              raw,
              external_id,
              what_embed
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO UPDATE
              SET start_date=EXCLUDED.start_date,
                  end_date=EXCLUDED.end_date,
                  place_id=EXCLUDED.place_id,
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
                normalized_tags,
                types,
                event.title or "",
                event.summary or "",
                json.dumps(event.raw or {}),
                event.external_id,
                emb,
            ),
        )
        # Replace event_contacts rows (DELETE + INSERT) in same transaction.
        # Auto-create stub contacts for any IDs that don't exist yet.
        cur.execute("DELETE FROM event_contacts WHERE event_id = %s", (event.id,))
        if people_ids:
            cur.execute(
                "SELECT contact_id FROM contacts WHERE contact_id = ANY(%s)",
                (people_ids,),
            )
            existing_ids = {row["contact_id"] for row in cur.fetchall()}
            missing_ids = [cid for cid in people_ids if cid not in existing_ids]
            if missing_ids:
                _ensure_stub_contacts(missing_ids)
            cur.executemany(
                "INSERT INTO event_contacts (event_id, contact_id) VALUES (%s, %s)",
                [(event.id, cid) for cid in people_ids],
            )
        conn.commit()
    _enqueue_event_tag_enrichment(event.id)


def _enqueue_event_tag_enrichment(event_id: str) -> None:
    try:
        import event_tag_jobs

        event_tag_jobs.enqueue_event_tag_enrichment(event_id)
    except Exception as exc:
        logger.warning("[ingest_event] Failed to queue tag enrichment for %s: %s", event_id, exc)


def generate_and_persist_event_tags(event_id: str) -> dict[str, Any]:
    cleaned_event_id = str(event_id or "").strip()
    if not cleaned_event_id:
        raise ValueError("event_id is required")

    event = _get_event_by_id(cleaned_event_id)
    if not event:
        return {"event_id": cleaned_event_id, "updated": False, "reason": "event_not_found"}

    raw_tags = list(event.get("tags") or [])
    existing_tags = _normalize_strings(raw_tags)
    suggested_tags = _suggest_event_tags(
        event.get("title"),
        event.get("summary"),
        existing_tags,
    )
    merged_tags = _merge_tag_lists(existing_tags, suggested_tags)
    if merged_tags == existing_tags and raw_tags == existing_tags:
        return {
            "event_id": cleaned_event_id,
            "updated": False,
            "reason": "no_new_tags",
            "tags": existing_tags,
        }

    embedding_payload = {
        "id": event.get("id"),
        "startDate": event.get("start_date"),
        "endDate": event.get("end_date"),
        "placeId": event.get("place_id"),
        "people": event.get("people") or [],
        "tags": merged_tags,
        "types": normalize_event_types(event.get("types") or []),
        "title": event.get("title") or "",
        "summary": event.get("summary") or "",
        "raw": event.get("raw") or {},
        "externalId": event.get("external_id"),
    }
    emb = _generate_event_embedding(embedding_payload)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE events
            SET tags = %s,
                what_embed = %s
            WHERE id = %s
            """,
            (merged_tags, emb, cleaned_event_id),
        )
        updated = cur.rowcount > 0
        conn.commit()

    return {
        "event_id": cleaned_event_id,
        "updated": updated,
        "tags": merged_tags,
        "suggested_tags": suggested_tags,
    }


def get_event_by_id(event_id: str | None) -> dict[str, Any] | None:
    """Public accessor for a single event row, including the raw JSON column."""
    return _get_event_by_id(event_id)


_EVENT_UPDATE_PATCH_KEYS = {
    "start_date",
    "end_date",
    "place_id",
    "people",
    "tags",
    "types",
    "title",
    "summary",
    "raw",
}


def update_event(event_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a patch to an existing event and persist via ingest_event.

    The patch is authoritative for any field that is explicitly present — lists
    replace existing values, scalars replace (including ``place_id=None`` to
    clear). Fields not in the patch are preserved from the existing row. The
    ``raw`` field, if supplied, is appended to an ``update_history`` audit
    array rather than overwriting the existing audit trail.
    """
    cleaned_event_id = str(event_id or "").strip()
    if not cleaned_event_id:
        raise ValueError("event_id is required")

    existing = _get_event_by_id(cleaned_event_id)
    if existing is None:
        raise ValueError(f"Event not found: {cleaned_event_id}")

    unknown_keys = set(patch.keys()) - _EVENT_UPDATE_PATCH_KEYS
    if unknown_keys:
        raise ValueError(f"Unknown event patch fields: {sorted(unknown_keys)}")

    merged_raw = dict(existing.get("raw") or {})
    incoming_raw = patch.get("raw")
    if isinstance(incoming_raw, dict) and incoming_raw:
        history = list(merged_raw.get("update_history") or [])
        history.append(
            {
                "updated_at": datetime.utcnow().isoformat(),
                **{key: value for key, value in incoming_raw.items() if key != "update_history"},
            }
        )
        merged_raw["update_history"] = history

    def _value(key: str, fallback: Any) -> Any:
        # Presence check is required: patch[key] == None must clear the field,
        # while key missing must preserve existing. patch.get would conflate them.
        return patch[key] if key in patch else fallback  # noqa: SIM401

    event_in = EventIn(
        id=cleaned_event_id,
        startDate=_value("start_date", existing.get("start_date")),
        endDate=_value("end_date", existing.get("end_date")),
        placeId=_value("place_id", existing.get("place_id")),
        people=list(_value("people", existing.get("people") or [])),
        tags=list(_value("tags", existing.get("tags") or [])),
        types=list(_value("types", existing.get("types") or ["generic"])),
        title=_value("title", existing.get("title") or "") or "",
        summary=_value("summary", existing.get("summary") or "") or "",
        raw=merged_raw,
        externalId=existing.get("external_id"),
    )
    ingest_event(event_in)
    return {"event_id": cleaned_event_id}


def delete_event(event_id: str) -> bool:
    cleaned_event_id = str(event_id or "").strip()
    if not cleaned_event_id:
        return False

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM event_contacts WHERE event_id = %s", (cleaned_event_id,))
        cur.execute("DELETE FROM todo_events WHERE event_id = %s", (cleaned_event_id,))
        cur.execute(
            """
            DELETE FROM events
            WHERE id = %s
            """,
            (cleaned_event_id,),
        )
        deleted = cur.rowcount > 0
        conn.commit()
        return deleted


def get_events(ids: list[str]) -> list[dict[str, Any]]:
    rows = fetch_events(ids)
    photos_by_event = event_photos_service.list_event_photos_for_events(ids)
    return [
        {
            "id": r["id"],
            "start_date": r["start_date"].isoformat() if r.get("start_date") else None,
            "end_date": r["end_date"].isoformat() if r.get("end_date") else None,
            "people": enrich_people(r.get("people"), r.get("_contact_names", {})),
            "tags": r["tags"],
            "types": r.get("types", []),
            "title": r.get("title"),
            "summary": r.get("summary"),
            "external_id": r.get("external_id"),
            "photos": photos_by_event.get(r["id"], []),
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


def get_event_action_items(event_id: str | None) -> list[dict[str, Any]]:
    event = _get_event_by_id(event_id)
    if not event:
        return []
    return _extract_event_action_items(event.get("raw"))


def _extract_event_action_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, dict):
        return []

    raw_items = raw.get("action_items")
    if not isinstance(raw_items, list):
        summary_result = raw.get("summary_result")
        if isinstance(summary_result, dict):
            raw_items = summary_result.get("action_items")
    if not isinstance(raw_items, list):
        return []

    action_items: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        task = _clean_optional_text(item.get("task"))
        if not task:
            continue
        action_items.append(
            {
                "task": task,
                "assignee_name": _clean_optional_text(item.get("assignee_name")),
                "assignee_email": contacts_service.normalize_email(item.get("assignee_email") or ""),
                "due_date": _clean_optional_text(item.get("due_date")),
                "evidence": _clean_optional_text(item.get("evidence")),
                "belongs_to_current_user": bool(item.get("belongs_to_current_user")),
            }
        )
    return action_items


def _normalize_todo_description(text: str | None) -> str:
    if not text:
        return ""
    squashed = " ".join(text.split())
    return squashed.strip().lower()


def _get_existing_todo_signatures(event_id: str | None) -> set[str]:
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
    signatures: set[str] = set()
    for row in rows:
        signature = _normalize_todo_description(row.get("description"))
        if signature:
            signatures.add(signature)
    return signatures


def _get_event_id_by_external_id(external_id: str | None) -> str | None:
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


def _get_event_by_id(event_id: str | None) -> dict[str, Any] | None:
    if not event_id:
        return None
    normalized = event_id.strip()
    if not normalized:
        return None
    from db import fetch_event_people

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              id,
              start_date,
              end_date,
              place_id,
              tags,
              types,
              title,
              summary,
              raw,
              external_id
            FROM events
            WHERE id = %s
            LIMIT 1
            """,
            (normalized,),
        )
        row = cur.fetchone()
        if not row:
            return None
        people_map = fetch_event_people(cur, [normalized])
        people = people_map.get(normalized, [])
        raw_data = row["raw"] or {}
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except json.JSONDecodeError:
                raw_data = {"content": raw_data}
        return {
            "id": row["id"],
            "start_date": row["start_date"],
            "end_date": row["end_date"],
            "place_id": row["place_id"],
            "people": people,
            "tags": row["tags"] or [],
            "types": row["types"] or [],
            "title": row["title"],
            "summary": row["summary"],
            "raw": raw_data,
            "external_id": row["external_id"],
            "photos": event_photos_service.list_event_photos(normalized),
        }


def _merge_text(existing: str | None, incoming: str | None) -> str:
    existing_value = (existing or "").strip()
    incoming_value = (incoming or "").strip()
    if not existing_value:
        return incoming_value
    if not incoming_value:
        return existing_value
    if existing_value == incoming_value:
        return existing_value
    return f"{existing_value}\n\n{incoming_value}"


def _merge_types(existing: Sequence[str], incoming: Sequence[str]) -> list[str]:
    merged = list(dict.fromkeys(normalize_event_types(existing) + normalize_event_types(incoming)))
    if any(item != "generic" for item in merged):
        merged = [item for item in merged if item != "generic"]
    return merged or ["generic"]


def _merge_event(
    existing: dict[str, Any],
    incoming: EventIn,
    *,
    mode: EventMergeMode = EventMergeMode.ADDITIVE,
) -> EventIn:
    if mode == EventMergeMode.AUTHORITATIVE_EXTERNAL:
        merged_start = incoming.start_date
        merged_end = incoming.end_date
        merged_place_id = incoming.place_id
        merged_people = list(dict.fromkeys(incoming.people or []))

        # Keep existing summary text because it may include richer notes from other sources.
        merged_summary = existing.get("summary") or incoming.summary or ""
    else:
        existing_end = existing.get("end_date")
        incoming_end = incoming.end_date
        merged_start = existing.get("start_date") or incoming.start_date
        merged_end = existing_end
        if incoming_end and (not existing_end or incoming_end > existing_end):
            merged_end = incoming_end
        merged_place_id = incoming.place_id or existing.get("place_id")
        merged_people = list(dict.fromkeys((existing.get("people") or []) + (incoming.people or [])))
        merged_summary = _merge_text(existing.get("summary"), incoming.summary)

    merged_tags = list(dict.fromkeys((existing.get("tags") or []) + (incoming.tags or [])))
    merged_raw = dict(existing.get("raw") or {})
    merged_raw.update(incoming.raw or {})

    return EventIn(
        id=existing.get("id") or incoming.id,
        startDate=merged_start,
        endDate=merged_end,
        placeId=merged_place_id,
        people=merged_people,
        tags=merged_tags,
        types=_merge_types(existing.get("types") or [], incoming.types or []),
        title=incoming.title or existing.get("title") or "",
        summary=merged_summary,
        raw=merged_raw,
        externalId=incoming.external_id or existing.get("external_id"),
    )


def _get_event_external_id(event_id: str | None) -> str | None:
    if not event_id:
        return None
    normalized = event_id.strip()
    if not normalized:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT external_id
            FROM events
            WHERE id = %s
            LIMIT 1
            """,
            (normalized,),
        )
        row = cur.fetchone()
        return row["external_id"] if row else None


def _find_matching_meeting_event(
    title: str | None,
    start_date: datetime | None,
) -> str | None:
    if not title or not start_date:
        return None
    normalized_title = title.strip()
    if not normalized_title:
        return None

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM events
            WHERE title = %s
              AND start_date::date = %s::date
            ORDER BY id
            """,
            (normalized_title, start_date),
        )
        rows = cur.fetchall()

    for row in rows:
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

    segments: list[str] = []

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
        formatted = ", ".join(
            str(tag).strip() for tag in tags if isinstance(tag, str) and tag.strip()
        )
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
