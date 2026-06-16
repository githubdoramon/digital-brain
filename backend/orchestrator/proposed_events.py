from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import atan2, cos, radians, sin, sqrt
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import events as events_service
from db import get_conn
from llm_helpers import build_json_schema_response_format, call_llm_json
from llm_json_schemas import PROPOSED_EVENT_ENRICHMENT_RESPONSE_SCHEMA
from observability.logger import get_runtime_logger
from schemas import EventIn
from search_normalization import normalize_search_text

logger = get_runtime_logger(__name__)

MIN_STAY_MINUTES = 15
UNKNOWN_PLACE_MIN_STAY_MINUTES = 30
PROPOSAL_TTL_DAYS = 7
HISTORY_LOOKBACK_DAYS = 90
MAX_HISTORY_EVENTS = 12
DAILY_SCAN_UTC_HOUR = 15
DAILY_SCAN_UTC_MINUTE = 50
HOME_TERMS = {"home", "house", "my home", "apartment", "flat", "residence"}


@dataclass
class StaySegment:
    start_at: datetime
    end_at: datetime
    samples: list[dict[str, Any]]
    place_id: str | None
    place_name: str | None
    city: str | None
    country: str | None
    lat: float
    lon: float
    signature: str


def analyze_user_day(
    *,
    user_email: str,
    target_date: date,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    tz = _resolve_timezone(timezone_name or _latest_timezone(user_email))
    day_start = datetime.combine(target_date, time.min, tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    start_utc = day_start.astimezone(timezone.utc)
    end_utc = day_end.astimezone(timezone.utc)

    logger.info(
        "[proposed_events] analyze_start user=%s date=%s timezone=%s window_utc=%s..%s",
        user_email,
        target_date.isoformat(),
        getattr(tz, "key", "UTC"),
        start_utc.isoformat(),
        end_utc.isoformat(),
    )
    expire_pending(user_email=user_email)
    rows = _fetch_locations(user_email=user_email, start_at=start_utc, end_at=end_utc)
    ignores = _fetch_ignores(user_email)
    segments = _build_stay_segments(rows, day_end=end_utc)

    created = 0
    skipped = 0
    skip_reasons: dict[str, int] = {}
    proposals: list[dict[str, Any]] = []
    for segment in segments:
        if _has_overlapping_event(
            start_at=segment.start_at,
            end_at=segment.end_at,
            user_email=user_email,
        ):
            skipped += 1
            skip_reasons["overlapping_event"] = skip_reasons.get("overlapping_event", 0) + 1
            continue
        candidate = _build_candidate(
            user_email=user_email,
            segment=segment,
            local_date=target_date,
            timezone_name=getattr(tz, "key", "UTC"),
            ignores=ignores,
        )
        if not candidate:
            skipped += 1
            reason = _segment_skip_reason(segment, ignores)
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            continue
        proposal = _insert_proposal(candidate)
        if proposal:
            created += 1
            proposals.append(proposal)
        else:
            skipped += 1
            skip_reasons["duplicate_proposal"] = skip_reasons.get("duplicate_proposal", 0) + 1

    result = {
        "created": created,
        "skipped": skipped,
        "skip_reasons": skip_reasons,
        "proposal_count": len(proposals),
        "proposals": proposals,
        "date": target_date.isoformat(),
        "timezone": getattr(tz, "key", "UTC"),
        "location_count": len(rows),
        "segment_count": len(segments),
    }
    logger.info(
        "[proposed_events] analyze_complete user=%s date=%s locations=%s segments=%s created=%s skipped=%s skip_reasons=%s",
        user_email,
        target_date.isoformat(),
        len(rows),
        len(segments),
        created,
        skipped,
        json.dumps(skip_reasons, sort_keys=True),
    )
    return result


def list_proposals(user_email: str, *, include_resolved: bool = False) -> list[dict[str, Any]]:
    expire_pending(user_email=user_email)
    status_clause = "" if include_resolved else "AND pe.status = 'pending'"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                pe.proposal_id,
                pe.status,
                pe.source,
                pe.local_date,
                pe.timezone,
                pe.start_at,
                pe.end_at,
                pe.duration_minutes,
                pe.place_id,
                pe.place_name,
                pe.city,
                pe.country,
                pe.lat,
                pe.lon,
                pe.confidence,
                pe.reason,
                pe.suggested_title,
                pe.suggested_summary,
                pe.suggested_contact_ids,
                pe.evidence,
                pe.accepted_event_id,
                pe.expires_at,
                pe.created_at,
                pe.updated_at,
                p.name AS canonical_place_name
            FROM proposed_events pe
            LEFT JOIN places p ON p.place_id = pe.place_id
            WHERE pe.user_email = %s
              {status_clause}
            ORDER BY
                CASE pe.status WHEN 'pending' THEN 0 ELSE 1 END,
                pe.start_at DESC
            LIMIT 100
            """,
            (user_email,),
        )
        rows = [dict(row) for row in cur.fetchall()]
    return [_serialize_proposal(row) for row in rows]


def dismiss_proposal(user_email: str, proposal_id: str) -> dict[str, Any]:
    return _set_status(user_email, proposal_id, "dismissed")


def ignore_proposal(user_email: str, proposal_id: str) -> dict[str, Any]:
    proposal = _get_owned_proposal(user_email, proposal_id)
    signature = str(proposal.get("ignored_signature") or "").strip()
    place_id = str(proposal.get("place_id") or "").strip()
    place_name = str(proposal.get("place_name") or "").strip()
    ignore_type = "location_signature"
    value = signature
    if place_id:
        ignore_type = "place_id"
        value = place_id
    elif place_name:
        ignore_type = "place_name"
        value = normalize_search_text(place_name)
    if not value:
        raise ValueError("Proposal does not have an ignoreable place signature")

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO proposed_event_ignores (user_email, ignore_type, value, reason)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_email, ignore_type, value) DO UPDATE
              SET reason = COALESCE(EXCLUDED.reason, proposed_event_ignores.reason)
            """,
            (user_email, ignore_type, value, "User ignored proposed event"),
        )
        cur.execute(
            """
            UPDATE proposed_events
            SET status = 'ignored', updated_at = NOW()
            WHERE user_email = %s AND proposal_id = %s AND status = 'pending'
            RETURNING *
            """,
            (user_email, proposal_id),
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise LookupError("Pending proposal not found")
    return _serialize_proposal(dict(row))


def accept_proposal(
    user_email: str,
    proposal_id: str,
    *,
    title: str | None = None,
    summary: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    contact_ids: list[str] | None = None,
) -> dict[str, Any]:
    proposal = _get_owned_proposal(user_email, proposal_id)
    if proposal.get("status") != "pending":
        raise ValueError("Only pending proposals can be accepted")

    event_id = f"event:proposed:{uuid4().hex[:12]}"
    resolved_contacts = list(
        dict.fromkeys(
            [
                str(item).strip()
                for item in (
                    contact_ids
                    if contact_ids is not None
                    else proposal.get("suggested_contact_ids") or []
                )
                if str(item).strip()
            ]
        )
    )
    event = EventIn(
        id=event_id,
        startDate=start_at or proposal["start_at"],
        endDate=end_at or proposal.get("end_at"),
        placeId=proposal.get("place_id"),
        people=resolved_contacts,
        tags=["proposed-event"],
        types=["personal"],
        title=(title or proposal.get("suggested_title") or "Untitled event").strip(),
        summary=(summary or proposal.get("suggested_summary") or "").strip(),
        raw={
            "source": "proposed_events",
            "proposal_id": proposal_id,
            "proposal_reason": proposal.get("reason"),
            "location_evidence": proposal.get("evidence") or {},
        },
    )
    events_service.ingest_event(event)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE proposed_events
            SET status = 'accepted',
                accepted_event_id = %s,
                updated_at = NOW()
            WHERE user_email = %s
              AND proposal_id = %s
              AND status = 'pending'
            RETURNING *
            """,
            (event_id, user_email, proposal_id),
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise LookupError("Pending proposal not found")
    serialized = _serialize_proposal(dict(row))
    serialized["event_id"] = event_id
    return serialized


def expire_pending(*, user_email: str | None = None) -> int:
    params: list[Any] = []
    user_clause = ""
    if user_email:
        user_clause = "AND user_email = %s"
        params.append(user_email)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE proposed_events
            SET status = 'expired', updated_at = NOW()
            WHERE status = 'pending'
              AND expires_at <= NOW()
              {user_clause}
            """,
            params,
        )
        updated = cur.rowcount
        conn.commit()
    return updated


def list_users_for_daily_scan() -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (user_email)
                user_email,
                timezone
            FROM user_location_history
            WHERE captured_at >= NOW() - INTERVAL '14 days'
            ORDER BY user_email, captured_at DESC, id DESC
            """
        )
        return [dict(row) for row in cur.fetchall()]


def should_run_daily_scan(user_email: str, *, now_utc: datetime | None = None) -> dict[str, Any] | None:
    timezone_name = _latest_timezone(user_email)
    tz = _resolve_timezone(timezone_name)
    now_utc_value = now_utc or datetime.now(timezone.utc)
    if now_utc_value.tzinfo is None:
        now_utc_value = now_utc_value.replace(tzinfo=timezone.utc)
    now_utc_value = now_utc_value.astimezone(timezone.utc)
    if (now_utc_value.hour, now_utc_value.minute) < (
        DAILY_SCAN_UTC_HOUR,
        DAILY_SCAN_UTC_MINUTE,
    ):
        return None
    now = now_utc_value.astimezone(tz)
    target_date = now.date()
    dedupe_key = f"proposed-events:{target_date.isoformat()}:{getattr(tz, 'key', 'UTC')}"
    return {
        "target_date": target_date,
        "timezone": getattr(tz, "key", "UTC"),
        "dedupe_key": dedupe_key,
    }


def describe_daily_scan_eligibility(
    user_email: str,
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    timezone_name = _latest_timezone(user_email)
    tz = _resolve_timezone(timezone_name)
    now_utc_value = now_utc or datetime.now(timezone.utc)
    if now_utc_value.tzinfo is None:
        now_utc_value = now_utc_value.replace(tzinfo=timezone.utc)
    now_utc_value = now_utc_value.astimezone(timezone.utc)
    now = now_utc_value.astimezone(tz)
    due = (now_utc_value.hour, now_utc_value.minute) >= (
        DAILY_SCAN_UTC_HOUR,
        DAILY_SCAN_UTC_MINUTE,
    )
    return {
        "user_email": user_email,
        "timezone": getattr(tz, "key", "UTC"),
        "utc_now": now_utc_value.isoformat(),
        "local_now": now.isoformat(),
        "due": due,
        "reason": "due" if due else "before_15_50_utc",
        "target_date": now.date().isoformat(),
    }


def _build_candidate(
    *,
    user_email: str,
    segment: StaySegment,
    local_date: date,
    timezone_name: str,
    ignores: set[tuple[str, str]],
) -> dict[str, Any] | None:
    duration_minutes = int((segment.end_at - segment.start_at).total_seconds() // 60)
    if duration_minutes < MIN_STAY_MINUTES:
        return None
    normalized_name = normalize_search_text(segment.place_name or "")
    if normalized_name in HOME_TERMS or ("place_name", normalized_name) in ignores:
        return None
    if segment.place_id and ("place_id", segment.place_id) in ignores:
        return None
    if ("location_signature", segment.signature) in ignores:
        return None

    is_known_place = bool(segment.place_id or segment.place_name)
    if not is_known_place and duration_minutes < UNKNOWN_PLACE_MIN_STAY_MINUTES:
        return None

    confidence = "high" if is_known_place and duration_minutes >= 30 else "medium"
    contacts = _suggest_contacts_for_place(segment.place_id)
    place_label = segment.place_name or "Unknown place"
    title = f"Visited {place_label}"
    reason = f"Stayed around {duration_minutes} minutes with no event during that time."
    candidate = {
        "proposal_id": f"proposed_event:{uuid4().hex}",
        "user_email": user_email,
        "local_date": local_date,
        "timezone": timezone_name,
        "start_at": segment.start_at,
        "end_at": segment.end_at,
        "duration_minutes": duration_minutes,
        "place_id": segment.place_id,
        "place_name": segment.place_name,
        "city": segment.city,
        "country": segment.country,
        "lat": segment.lat,
        "lon": segment.lon,
        "confidence": confidence,
        "reason": reason,
        "suggested_title": title,
        "suggested_summary": reason,
        "suggested_contact_ids": contacts,
        "ignored_signature": segment.signature,
        "evidence": {
            "sample_count": len(segment.samples),
            "first_captured_at": segment.start_at.isoformat(),
            "last_captured_at": segment.samples[-1]["captured_at"].isoformat(),
            "source": "user_location_history",
        },
        "expires_at": datetime.now(timezone.utc) + timedelta(days=PROPOSAL_TTL_DAYS),
    }
    return _enrich_candidate_with_history(candidate, segment=segment, timezone_name=timezone_name)


def _segment_skip_reason(segment: StaySegment, ignores: set[tuple[str, str]]) -> str:
    duration_minutes = int((segment.end_at - segment.start_at).total_seconds() // 60)
    if duration_minutes < MIN_STAY_MINUTES:
        return "short_stay"
    normalized_name = normalize_search_text(segment.place_name or "")
    if normalized_name in HOME_TERMS:
        return "home_like_place"
    if normalized_name and ("place_name", normalized_name) in ignores:
        return "ignored_place_name"
    if segment.place_id and ("place_id", segment.place_id) in ignores:
        return "ignored_place_id"
    if ("location_signature", segment.signature) in ignores:
        return "ignored_location_signature"
    is_known_place = bool(segment.place_id or segment.place_name)
    if not is_known_place and duration_minutes < UNKNOWN_PLACE_MIN_STAY_MINUTES:
        return "unknown_place_short_stay"
    return "candidate_filtered"


def _insert_proposal(candidate: dict[str, Any]) -> dict[str, Any] | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO proposed_events (
                proposal_id,
                user_email,
                local_date,
                timezone,
                start_at,
                end_at,
                duration_minutes,
                place_id,
                place_name,
                city,
                country,
                lat,
                lon,
                confidence,
                reason,
                suggested_title,
                suggested_summary,
                suggested_contact_ids,
                ignored_signature,
                evidence,
                expires_at
            )
            VALUES (
                %(proposal_id)s,
                %(user_email)s,
                %(local_date)s,
                %(timezone)s,
                %(start_at)s,
                %(end_at)s,
                %(duration_minutes)s,
                %(place_id)s,
                %(place_name)s,
                %(city)s,
                %(country)s,
                %(lat)s,
                %(lon)s,
                %(confidence)s,
                %(reason)s,
                %(suggested_title)s,
                %(suggested_summary)s,
                %(suggested_contact_ids)s,
                %(ignored_signature)s,
                %(evidence)s::jsonb,
                %(expires_at)s
            )
            ON CONFLICT DO NOTHING
            RETURNING *
            """,
            {**candidate, "evidence": json.dumps(candidate["evidence"])},
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        logger.info(
            "[proposed_events] proposal_duplicate user=%s date=%s start=%s end=%s place_id=%s signature=%s",
            candidate.get("user_email"),
            candidate.get("local_date"),
            _iso(candidate.get("start_at")),
            _iso(candidate.get("end_at")),
            candidate.get("place_id"),
            candidate.get("ignored_signature"),
        )
        return None
    logger.info(
        "[proposed_events] proposal_created user=%s proposal_id=%s title=%s confidence=%s start=%s end=%s place_id=%s",
        candidate.get("user_email"),
        row.get("proposal_id"),
        row.get("suggested_title"),
        row.get("confidence"),
        _iso(row.get("start_at")),
        _iso(row.get("end_at")),
        row.get("place_id"),
    )
    return _serialize_proposal(dict(row))


def _enrich_candidate_with_history(
    candidate: dict[str, Any],
    *,
    segment: StaySegment,
    timezone_name: str,
) -> dict[str, Any]:
    context = _build_history_context(segment=segment, timezone_name=timezone_name)
    candidate["evidence"]["history"] = {
        "linked_contact_count": len(context["linked_contacts"]),
        "recent_event_count": len(context["recent_events"]),
        "recurrence": context["recurrence"],
    }
    allowed_contact_ids = {
        str(contact.get("contact_id") or "").strip()
        for contact in context["linked_contacts"]
    }
    for event in context["recent_events"]:
        for person in event.get("people") or []:
            contact_id = str(person.get("contact_id") or "").strip()
            if contact_id:
                allowed_contact_ids.add(contact_id)

    if not context["linked_contacts"] and not context["recent_events"]:
        logger.info(
            "[proposed_events] enrichment_skipped_no_history place_id=%s place_name=%s",
            candidate.get("place_id"),
            candidate.get("place_name"),
        )
        return candidate

    try:
        logger.info(
            "[proposed_events] enrichment_start place_id=%s place_name=%s linked_contacts=%s recent_events=%s",
            candidate.get("place_id"),
            candidate.get("place_name"),
            len(context["linked_contacts"]),
            len(context["recent_events"]),
        )
        enriched = call_llm_json(
            _build_enrichment_prompt(candidate, context),
            system_prompt=(
                "You enrich proposed personal memory events using only provided evidence. "
                "Use the smart model reasoning to infer likely title, people, recurrence, and summary, "
                "but never invent contacts or facts. Return schema-valid JSON only."
            ),
            use_fast_model=False,
            reasoning_effort="high",
            timeout=90,
            temperature=0.0,
            response_format=build_json_schema_response_format(
                name="proposed_event_enrichment",
                schema=PROPOSED_EVENT_ENRICHMENT_RESPONSE_SCHEMA,
            ),
        )
    except Exception as exc:
        logger.warning("[proposed_events] LLM enrichment unavailable: %s", exc)
        return _apply_deterministic_history_enrichment(candidate, context, allowed_contact_ids)

    logger.info(
        "[proposed_events] enrichment_complete place_id=%s title=%s confidence=%s suggested_contacts=%s",
        candidate.get("place_id"),
        enriched.get("suggested_title"),
        enriched.get("confidence"),
        len(enriched.get("suggested_contact_ids") or []),
    )
    return _apply_llm_enrichment(candidate, enriched, context, allowed_contact_ids)


def _build_history_context(*, segment: StaySegment, timezone_name: str) -> dict[str, Any]:
    linked_contacts = _linked_contacts_for_place(segment.place_id)
    recent_events = _recent_events_for_place(segment=segment)
    recurrence = _build_recurrence_summary(segment=segment, events=recent_events, timezone_name=timezone_name)
    return {
        "linked_contacts": linked_contacts,
        "recent_events": recent_events,
        "recurrence": recurrence,
    }


def _linked_contacts_for_place(place_id: str | None) -> list[dict[str, Any]]:
    if not place_id:
        return []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                cp.contact_id,
                cp.role,
                cp.confidence,
                c.display_name
            FROM contact_places cp
            JOIN contacts c ON c.contact_id = cp.contact_id
            WHERE cp.place_id = %s
            ORDER BY c.display_name NULLS LAST, cp.contact_id
            LIMIT 12
            """,
            (place_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def _recent_events_for_place(*, segment: StaySegment) -> list[dict[str, Any]]:
    params: list[Any] = [segment.start_at, segment.start_at - timedelta(days=HISTORY_LOOKBACK_DAYS)]
    place_clause = ""
    if segment.place_id:
        place_clause = "e.place_id = %s"
        params.append(segment.place_id)
    elif segment.place_name:
        place_clause = "unaccent(COALESCE(p.name, '')) ILIKE unaccent(%s)"
        params.append(segment.place_name)
    else:
        return []

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                e.id,
                e.title,
                e.summary,
                e.start_date,
                e.end_date,
                e.tags,
                e.types,
                e.place_id,
                p.name AS place_name
            FROM events e
            LEFT JOIN places p ON p.place_id = e.place_id
            WHERE e.start_date < %s
              AND e.start_date >= %s
              AND {place_clause}
            ORDER BY e.start_date DESC
            LIMIT %s
            """,
            (*params, MAX_HISTORY_EVENTS),
        )
        rows = [dict(row) for row in cur.fetchall()]
        event_ids = [str(row["id"]) for row in rows]
        people_by_event = _fetch_people_for_events(cur, event_ids)
    for row in rows:
        row["people"] = people_by_event.get(str(row["id"]), [])
        row["start_date"] = _iso(row.get("start_date"))
        row["end_date"] = _iso(row.get("end_date"))
    return rows


def _fetch_people_for_events(cur: Any, event_ids: list[str]) -> dict[str, list[dict[str, str]]]:
    if not event_ids:
        return {}
    cur.execute(
        """
        SELECT ec.event_id, ec.contact_id, c.display_name
        FROM event_contacts ec
        LEFT JOIN contacts c ON c.contact_id = ec.contact_id
        WHERE ec.event_id = ANY(%s)
        ORDER BY c.display_name NULLS LAST, ec.contact_id
        """,
        (event_ids,),
    )
    people: dict[str, list[dict[str, str]]] = {}
    for row in cur.fetchall():
        event_id = str(row["event_id"])
        contact_id = str(row["contact_id"])
        people.setdefault(event_id, []).append(
            {
                "contact_id": contact_id,
                "display_name": str(row.get("display_name") or contact_id),
            }
        )
    return people


def _build_recurrence_summary(
    *,
    segment: StaySegment,
    events: list[dict[str, Any]],
    timezone_name: str,
) -> dict[str, Any]:
    tz = _resolve_timezone(timezone_name)
    segment_local = segment.start_at.astimezone(tz)
    segment_weekday = segment_local.weekday()
    segment_hour = segment_local.hour
    same_weekday = 0
    same_time_bucket = 0
    titles: dict[str, int] = {}
    contact_counts: dict[str, int] = {}
    for event in events:
        event_start = _parse_datetime(event.get("start_date"))
        if event_start:
            local_start = event_start.astimezone(tz)
            if local_start.weekday() == segment_weekday:
                same_weekday += 1
            if abs(local_start.hour - segment_hour) <= 1:
                same_time_bucket += 1
        title_key = normalize_search_text(event.get("title") or "")
        if title_key:
            titles[title_key] = titles.get(title_key, 0) + 1
        for person in event.get("people") or []:
            contact_id = str(person.get("contact_id") or "").strip()
            if contact_id:
                contact_counts[contact_id] = contact_counts.get(contact_id, 0) + 1
    return {
        "lookback_days": HISTORY_LOOKBACK_DAYS,
        "same_place_event_count": len(events),
        "same_weekday_count": same_weekday,
        "same_time_bucket_count": same_time_bucket,
        "common_title": max(titles.items(), key=lambda item: item[1])[0] if titles else None,
        "common_contact_ids": [
            contact_id
            for contact_id, _count in sorted(
                contact_counts.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:5]
        ],
    }


def _build_enrichment_prompt(candidate: dict[str, Any], context: dict[str, Any]) -> str:
    prompt_payload = {
        "candidate": {
            "start_at": _iso(candidate.get("start_at")),
            "end_at": _iso(candidate.get("end_at")),
            "duration_minutes": candidate.get("duration_minutes"),
            "place_id": candidate.get("place_id"),
            "place_name": candidate.get("place_name"),
            "city": candidate.get("city"),
            "country": candidate.get("country"),
            "current_suggested_title": candidate.get("suggested_title"),
            "current_suggested_summary": candidate.get("suggested_summary"),
            "current_suggested_contact_ids": candidate.get("suggested_contact_ids"),
        },
        "linked_contacts_for_place": context["linked_contacts"],
        "recent_same_place_events": context["recent_events"],
        "recurrence": context["recurrence"],
        "rules": [
            "Only return contact IDs present in linked_contacts_for_place or recent_same_place_events.people.",
            "Use null recurrence_hint when history is too thin.",
            "Prefer a specific recurring title over generic 'Visited ...' only when evidence supports it.",
            "Keep suggested_summary concise and evidence-grounded.",
            "Confidence must remain medium unless recurrence/contact evidence is strong.",
        ],
    }
    return json.dumps(prompt_payload, ensure_ascii=False, default=str)


def _apply_llm_enrichment(
    candidate: dict[str, Any],
    enriched: dict[str, Any],
    context: dict[str, Any],
    allowed_contact_ids: set[str],
) -> dict[str, Any]:
    title = " ".join(str(enriched.get("suggested_title") or "").split()).strip()
    summary = " ".join(str(enriched.get("suggested_summary") or "").split()).strip()
    reason = " ".join(str(enriched.get("reason") or "").split()).strip()
    confidence = str(enriched.get("confidence") or candidate.get("confidence") or "medium").strip().lower()
    if confidence not in {"medium", "high"}:
        confidence = str(candidate.get("confidence") or "medium")
    contact_ids = _validated_contact_ids(enriched.get("suggested_contact_ids"), allowed_contact_ids)

    if title:
        candidate["suggested_title"] = title
    if summary:
        candidate["suggested_summary"] = summary
    if reason:
        candidate["reason"] = reason
    if contact_ids:
        candidate["suggested_contact_ids"] = contact_ids
    candidate["confidence"] = confidence
    candidate["evidence"]["llm_enrichment"] = {
        "used": True,
        "recurrence_hint": enriched.get("recurrence_hint"),
        "recent_event_ids": [event.get("id") for event in context["recent_events"]],
    }
    return candidate


def _apply_deterministic_history_enrichment(
    candidate: dict[str, Any],
    context: dict[str, Any],
    allowed_contact_ids: set[str],
) -> dict[str, Any]:
    recurrence = context["recurrence"]
    common_contacts = _validated_contact_ids(recurrence.get("common_contact_ids"), allowed_contact_ids)
    if common_contacts:
        candidate["suggested_contact_ids"] = common_contacts
    common_title = str(recurrence.get("common_title") or "").strip()
    if common_title and int(recurrence.get("same_place_event_count") or 0) >= 2:
        candidate["suggested_title"] = common_title.title()
    if int(recurrence.get("same_time_bucket_count") or 0) >= 2:
        candidate["confidence"] = "high"
        candidate["reason"] = (
            f"{candidate['reason']} Similar visits at this place and time appear in recent history."
        )
    candidate["evidence"]["llm_enrichment"] = {
        "used": False,
        "fallback": "deterministic_history",
        "recent_event_ids": [event.get("id") for event in context["recent_events"]],
    }
    return candidate


def _validated_contact_ids(value: Any, allowed_contact_ids: set[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    contact_ids: list[str] = []
    for item in value:
        contact_id = str(item or "").strip()
        if contact_id and contact_id in allowed_contact_ids and contact_id not in contact_ids:
            contact_ids.append(contact_id)
    return contact_ids


def _fetch_locations(*, user_email: str, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, lat, lon, accuracy_m, captured_at, source, timezone, place_name, city, country
            FROM user_location_history
            WHERE user_email = %s
              AND captured_at >= %s
              AND captured_at < %s
            ORDER BY captured_at ASC, id ASC
            """,
            (user_email, start_at, end_at),
        )
        rows = [dict(row) for row in cur.fetchall()]
    return [_enrich_location(row) for row in rows]


def _enrich_location(row: dict[str, Any]) -> dict[str, Any]:
    matched = _nearest_known_place(float(row["lat"]), float(row["lon"]), row.get("accuracy_m"))
    if matched:
        row.update(
            {
                "place_id": matched.get("place_id"),
                "place_name": row.get("place_name") or matched.get("name"),
                "city": row.get("city") or matched.get("city"),
                "country": row.get("country") or matched.get("country"),
            }
        )
    else:
        row["place_id"] = None
    return row


def _build_stay_segments(rows: list[dict[str, Any]], *, day_end: datetime) -> list[StaySegment]:
    if len(rows) < 2:
        return []
    segments: list[StaySegment] = []
    current: list[dict[str, Any]] = [rows[0]]
    current_signature = _location_signature(rows[0])
    for row in rows[1:]:
        signature = _location_signature(row)
        if signature == current_signature:
            current.append(row)
            continue
        segments.append(_make_segment(current, next_start=row["captured_at"]))
        current = [row]
        current_signature = signature
    segments.append(_make_segment(current, next_start=min(day_end, current[-1]["captured_at"])))
    return segments


def _make_segment(rows: list[dict[str, Any]], *, next_start: datetime) -> StaySegment:
    first = rows[0]
    last = rows[-1]
    end_at = max(next_start, last["captured_at"])
    return StaySegment(
        start_at=first["captured_at"],
        end_at=end_at,
        samples=rows,
        place_id=first.get("place_id"),
        place_name=first.get("place_name"),
        city=first.get("city"),
        country=first.get("country"),
        lat=float(first["lat"]),
        lon=float(first["lon"]),
        signature=_location_signature(first),
    )


def _location_signature(row: dict[str, Any]) -> str:
    place_id = str(row.get("place_id") or "").strip()
    if place_id:
        return f"place:{place_id}"
    place_name = normalize_search_text(row.get("place_name") or "")
    if place_name:
        return f"name:{place_name}"
    lat_bucket = round(float(row.get("lat") or 0.0), 3)
    lon_bucket = round(float(row.get("lon") or 0.0), 3)
    return f"geo:{lat_bucket}:{lon_bucket}"


def _nearest_known_place(lat: float, lon: float, accuracy_m: Any) -> dict[str, Any] | None:
    threshold = max(45.0, min(float(accuracy_m or 0.0), 150.0))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT place_id, name, city, country, lat, lon
            FROM places
            WHERE lat IS NOT NULL AND lon IS NOT NULL
            """
        )
        rows = [dict(row) for row in cur.fetchall()]
    nearest: dict[str, Any] | None = None
    nearest_distance: float | None = None
    for row in rows:
        distance = _distance_meters(lat, lon, float(row["lat"]), float(row["lon"]))
        if nearest_distance is None or distance < nearest_distance:
            nearest = row
            nearest_distance = distance
    if nearest is None or nearest_distance is None or nearest_distance > threshold:
        return None
    return nearest


def _has_overlapping_event(*, start_at: datetime, end_at: datetime, user_email: str) -> bool:
    _ = user_email
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM events e
            WHERE e.start_date < %s
              AND COALESCE(e.end_date, e.start_date + INTERVAL '30 minutes') > %s
            LIMIT 1
            """,
            (end_at, start_at),
        )
        return cur.fetchone() is not None


def _suggest_contacts_for_place(place_id: str | None) -> list[str]:
    if not place_id:
        return []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id
            FROM contact_places
            WHERE place_id = %s
            ORDER BY
              CASE WHEN normalize_search_text_for_sort(role) = 'home' THEN 0 ELSE 1 END,
              contact_id
            LIMIT 8
            """.replace("normalize_search_text_for_sort(role)", "unaccent(LOWER(COALESCE(role, '')))"),
            (place_id,),
        )
        return [row["contact_id"] for row in cur.fetchall()]


def _fetch_ignores(user_email: str) -> set[tuple[str, str]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT ignore_type, value
            FROM proposed_event_ignores
            WHERE user_email = %s
            """,
            (user_email,),
        )
        return {(str(row["ignore_type"]), str(row["value"])) for row in cur.fetchall()}


def _get_owned_proposal(user_email: str, proposal_id: str) -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM proposed_events
            WHERE user_email = %s AND proposal_id = %s
            LIMIT 1
            """,
            (user_email, proposal_id),
        )
        row = cur.fetchone()
    if not row:
        raise LookupError("Proposal not found")
    return dict(row)


def _set_status(user_email: str, proposal_id: str, status: str) -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE proposed_events
            SET status = %s, updated_at = NOW()
            WHERE user_email = %s AND proposal_id = %s AND status = 'pending'
            RETURNING *
            """,
            (status, user_email, proposal_id),
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise LookupError("Pending proposal not found")
    return _serialize_proposal(dict(row))


def _latest_timezone(user_email: str) -> str | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT timezone
            FROM user_location_history
            WHERE user_email = %s AND timezone IS NOT NULL AND timezone <> ''
            ORDER BY captured_at DESC, id DESC
            LIMIT 1
            """,
            (user_email,),
        )
        row = cur.fetchone()
    return str(row["timezone"]) if row and row.get("timezone") else None


def _resolve_timezone(timezone_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _distance_meters(first_lat: float, first_lon: float, second_lat: float, second_lon: float) -> float:
    earth_radius_meters = 6_371_000
    lat_delta = radians(second_lat - first_lat)
    lon_delta = radians(second_lon - first_lon)
    first_lat_radians = radians(first_lat)
    second_lat_radians = radians(second_lat)
    haversine = sin(lat_delta / 2) ** 2 + cos(first_lat_radians) * cos(second_lat_radians) * sin(lon_delta / 2) ** 2
    arc = 2 * atan2(sqrt(haversine), sqrt(1 - haversine))
    return earth_radius_meters * arc


def _iso(value: Any) -> str | None:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _serialize_proposal(row: dict[str, Any]) -> dict[str, Any]:
    output = dict(row)
    for key in ("local_date", "start_at", "end_at", "expires_at", "created_at", "updated_at"):
        value = output.get(key)
        if hasattr(value, "isoformat"):
            output[key] = value.isoformat()
    if output.get("canonical_place_name") and not output.get("place_name"):
        output["place_name"] = output["canonical_place_name"]
    return output
