from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import atan2, cos, radians, sin, sqrt
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import events as events_service
import google_place_cache
import google_places
import places as places_service
import web_tools
from db import get_conn
from llm_helpers import build_json_schema_response_format, call_llm_json
from llm_json_schemas import (
    PROPOSED_EVENT_ENRICHMENT_RESPONSE_SCHEMA,
    PROPOSED_EVENT_OVERLAP_RESPONSE_SCHEMA,
)
from observability.logger import get_runtime_logger
from scheduled_jobs import PROPOSED_EVENTS_DAILY
from schemas import EventIn, PlaceIn
from search_normalization import normalize_search_text

logger = get_runtime_logger(__name__)

MIN_STAY_MINUTES = 15
UNKNOWN_PLACE_MIN_STAY_MINUTES = 30
PRE_SLEEP_ACTIVITY_MIN_MINUTES = 180
PROPOSAL_TTL_DAYS = 7
HISTORY_LOOKBACK_DAYS = 90
MAX_HISTORY_EVENTS = 12
DAILY_SCAN_UTC_HOUR = PROPOSED_EVENTS_DAILY.time_utc.hour if PROPOSED_EVENTS_DAILY.time_utc else 0
DAILY_SCAN_UTC_MINUTE = PROPOSED_EVENTS_DAILY.time_utc.minute if PROPOSED_EVENTS_DAILY.time_utc else 0
DAILY_SCAN_LOOKBACK_DAYS = 2
HOME_TERMS = {"home", "house", "my home", "apartment", "flat", "residence"}
ALL_DAY_EVENT_MIN_HOURS = 18
OVERLAP_DISAMBIGUATION_TIMEOUT_SECONDS = 45
PLACE_WEB_SEARCH_MAX_RESULTS = 3
PLACE_DESCRIPTION_MIN_CHARS = 80
PLACE_DESCRIPTION_MAX_CHARS = 600
PLACE_CLUSTER_RADIUS_M = 75.0
PLACE_QUERY_MAX_POINTS = 3
PLACE_QUERY_RADIUS_M = 100.0


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
    known_place_confidence: str | None = None


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
    result = _analyze_segments(
        user_email=user_email,
        timezone_name=getattr(tz, "key", "UTC"),
        rows=rows,
        segments=segments,
        ignores=ignores,
        local_date=target_date,
    )
    result.update(
        {
            "date": target_date.isoformat(),
            "timezone": getattr(tz, "key", "UTC"),
        }
    )
    logger.info(
        "[proposed_events] analyze_complete user=%s date=%s locations=%s segments=%s created=%s skipped=%s skip_reasons=%s",
        user_email,
        target_date.isoformat(),
        len(rows),
        len(segments),
        result["created"],
        result["skipped"],
        json.dumps(result["skip_reasons"], sort_keys=True),
    )
    return result


def _analyze_segments(
    *,
    user_email: str,
    timezone_name: str,
    rows: list[dict[str, Any]],
    segments: list[StaySegment],
    ignores: set[tuple[str, str]],
    local_date: date | None = None,
) -> dict[str, Any]:
    tz = _resolve_timezone(timezone_name)

    created = 0
    skipped = 0
    skip_reasons: dict[str, int] = {}
    proposals: list[dict[str, Any]] = []
    for segment in segments:
        reason = _segment_skip_reason(segment, ignores)
        if reason != "candidate_filtered":
            skipped += 1
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            continue
        candidate_segments = _proposal_candidate_segments(segment, timezone_name=timezone_name)
        for candidate_segment in candidate_segments:
            if _find_blocking_overlapping_events(segment=candidate_segment, user_email=user_email):
                skipped += 1
                skip_reasons["overlapping_event"] = skip_reasons.get("overlapping_event", 0) + 1
                continue
            candidate = _build_candidate(
                user_email=user_email,
                segment=candidate_segment,
                local_date=local_date or candidate_segment.start_at.astimezone(tz).date(),
                timezone_name=timezone_name,
                ignores=ignores,
            )
            if not candidate:
                skipped += 1
                reason = _segment_skip_reason(candidate_segment, ignores)
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                continue
            proposal = _insert_proposal(candidate)
            if proposal:
                created += 1
                proposals.append(proposal)
            else:
                skipped += 1
                skip_reasons["duplicate_proposal"] = skip_reasons.get("duplicate_proposal", 0) + 1

    return {
        "created": created,
        "skipped": skipped,
        "skip_reasons": skip_reasons,
        "proposal_count": len(proposals),
        "proposals": proposals,
        "location_count": len(rows),
        "segment_count": len(segments),
    }


def analyze_user_window(
    *,
    user_email: str,
    target_date: date,
    timezone_name: str | None = None,
    lookback_days: int = DAILY_SCAN_LOOKBACK_DAYS,
) -> dict[str, Any]:
    tz = _resolve_timezone(timezone_name or _latest_timezone(user_email))
    scan_dates = daily_scan_dates(target_date, lookback_days=lookback_days)
    window_start = datetime.combine(scan_dates[-1], time.min, tzinfo=tz)
    window_end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=tz)
    start_utc = window_start.astimezone(timezone.utc)
    end_utc = window_end.astimezone(timezone.utc)
    logger.info(
        "[proposed_events] analyze_window_start user=%s dates=%s..%s timezone=%s window_utc=%s..%s",
        user_email,
        scan_dates[-1].isoformat(),
        scan_dates[0].isoformat(),
        getattr(tz, "key", "UTC"),
        start_utc.isoformat(),
        end_utc.isoformat(),
    )
    expire_pending(user_email=user_email)
    rows = _fetch_locations(user_email=user_email, start_at=start_utc, end_at=end_utc)
    ignores = _fetch_ignores(user_email)
    segments = _build_stay_segments(rows, day_end=end_utc)
    result = _analyze_segments(
        user_email=user_email,
        timezone_name=getattr(tz, "key", "UTC"),
        rows=rows,
        segments=segments,
        ignores=ignores,
    )
    result.update(
        {
            "date": target_date.isoformat(),
            "scan_start_date": scan_dates[-1].isoformat(),
            "scan_end_date": scan_dates[0].isoformat(),
            "timezone": getattr(tz, "key", "UTC"),
            "scanned_dates": [scan_date.isoformat() for scan_date in scan_dates],
        }
    )
    logger.info(
        "[proposed_events] analyze_window_complete user=%s dates=%s..%s locations=%s segments=%s created=%s skipped=%s skip_reasons=%s",
        user_email,
        scan_dates[-1].isoformat(),
        scan_dates[0].isoformat(),
        len(rows),
        len(segments),
        result["created"],
        result["skipped"],
        json.dumps(result["skip_reasons"], sort_keys=True),
    )
    return result


def daily_scan_dates(target_date: date, *, lookback_days: int = DAILY_SCAN_LOOKBACK_DAYS) -> list[date]:
    bounded_lookback_days = max(1, int(lookback_days))
    return [target_date - timedelta(days=offset) for offset in range(bounded_lookback_days)]


def current_local_date(timezone_name: str | None) -> date:
    return datetime.now(_resolve_timezone(timezone_name)).date()


def get_day_timeline(
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

    rows = _fetch_locations(user_email=user_email, start_at=start_utc, end_at=end_utc)
    ignores = _fetch_ignores(user_email)
    segments = _build_stay_segments(rows, day_end=end_utc)
    return {
        "date": target_date.isoformat(),
        "timezone": getattr(tz, "key", "UTC"),
        "window": {
            "local_start": day_start.isoformat(),
            "local_end": day_end.isoformat(),
            "utc_start": start_utc.isoformat(),
            "utc_end": end_utc.isoformat(),
        },
        "location_count": len(rows),
        "segment_count": len(segments),
        "locations": [_serialize_location(row) for row in rows],
        "segments": [_serialize_timeline_segment(segment, ignores=ignores, user_email=user_email) for segment in segments],
        "proposals": _fetch_day_proposals(user_email=user_email, target_date=target_date),
    }


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
    place_candidate_id: str | None = None,
) -> dict[str, Any]:
    proposal = _get_owned_proposal(user_email, proposal_id)
    if proposal.get("status") != "pending":
        raise ValueError("Only pending proposals can be accepted")

    selected_place_id = _materialize_selected_place(proposal, place_candidate_id)

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
        placeId=selected_place_id,
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
                place_id = %s,
                updated_at = NOW()
            WHERE user_email = %s
              AND proposal_id = %s
              AND status = 'pending'
            RETURNING *
            """,
            (event_id, selected_place_id, user_email, proposal_id),
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise LookupError("Pending proposal not found")
    serialized = _serialize_proposal(dict(row))
    serialized["event_id"] = event_id
    return serialized


def _materialize_selected_place(proposal: dict[str, Any], place_candidate_id: str | None) -> str | None:
    existing_place_id = str(proposal.get("place_id") or "").strip() or None
    selected_id = str(place_candidate_id or "").strip()
    if not selected_id:
        return existing_place_id

    evidence = proposal.get("evidence")
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            evidence = {}
    if not isinstance(evidence, dict):
        evidence = {}
    candidates = evidence.get("place_candidates")
    if not isinstance(candidates, list):
        raise ValueError("Selected place candidate is not available")
    candidate = next(
        (
            item
            for item in candidates
            if isinstance(item, dict)
            and str(item.get("provider_place_id") or "").strip() == selected_id
        ),
        None,
    )
    if candidate is None:
        raise ValueError("Selected place candidate is not available")

    linked_place_id = google_place_cache.get_canonical_place_id(selected_id)
    if linked_place_id:
        return linked_place_id

    cached_candidate = google_place_cache.get_candidate(selected_id) or candidate
    title = str(cached_candidate.get("title") or "").strip()
    lat = _safe_optional_float(cached_candidate.get("lat"))
    lon = _safe_optional_float(cached_candidate.get("lon"))
    if not title or lat is None or lon is None:
        raise ValueError("Selected place candidate is incomplete")

    internal_place_id = f"plc_{_safe_place_slug(title)}_{uuid4().hex[:6]}"
    places_service.ingest_place(
        PlaceIn(
            place_id=internal_place_id,
            name=title,
            address=str(cached_candidate.get("formatted_address") or "").strip() or None,
            city=str(cached_candidate.get("city") or "").strip() or None,
            country=str(cached_candidate.get("country") or "").strip() or None,
            lat=lat,
            lon=lon,
        )
    )
    google_place_cache.link_canonical_place(selected_id, internal_place_id)
    return internal_place_id


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
        "reason": "due" if due else "before_daily_cutoff_utc",
        "target_date": now.date().isoformat(),
    }


def _proposal_candidate_segments(segment: StaySegment, *, timezone_name: str) -> list[StaySegment]:
    split_at = _activity_sleep_split_at(segment, timezone_name=timezone_name)
    if split_at is None:
        return [segment]

    candidate_segments: list[StaySegment] = []
    activity_minutes = int((split_at - segment.start_at).total_seconds() // 60)
    if activity_minutes >= PRE_SLEEP_ACTIVITY_MIN_MINUTES:
        candidate_segments.append(_copy_segment_window(segment, start_at=segment.start_at, end_at=split_at))
    sleep_segment = _copy_segment_window(segment, start_at=split_at, end_at=segment.end_at)
    candidate_segments.append(sleep_segment)
    return candidate_segments


def _activity_sleep_split_at(segment: StaySegment, *, timezone_name: str) -> datetime | None:
    tz = _resolve_timezone(timezone_name)
    local_start = segment.start_at.astimezone(tz)
    local_end = segment.end_at.astimezone(tz)
    if local_start.date() == local_end.date():
        return None
    if not (4 <= local_end.hour <= 11):
        return None
    split_local = datetime.combine(local_start.date(), time(hour=22), tzinfo=tz)
    split_at = split_local.astimezone(segment.start_at.tzinfo or timezone.utc)
    if not (segment.start_at < split_at < segment.end_at):
        return None

    activity_minutes = int((split_at - segment.start_at).total_seconds() // 60)
    sleep_minutes = int((segment.end_at - split_at).total_seconds() // 60)
    if activity_minutes < MIN_STAY_MINUTES or sleep_minutes < 240:
        return None
    return split_at


def _copy_segment_window(segment: StaySegment, *, start_at: datetime, end_at: datetime) -> StaySegment:
    samples = [
        sample
        for sample in segment.samples
        if start_at <= _parse_datetime(sample.get("captured_at")) <= end_at
    ]
    if not samples:
        samples = segment.samples
    return StaySegment(
        start_at=start_at,
        end_at=end_at,
        samples=samples,
        place_id=segment.place_id,
        place_name=segment.place_name,
        city=segment.city,
        country=segment.country,
        lat=segment.lat,
        lon=segment.lon,
        signature=segment.signature,
        known_place_confidence=segment.known_place_confidence,
    )


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
    duration_label = _humanize_duration_minutes(duration_minutes)
    time_context = _build_time_context(segment.start_at, segment.end_at, timezone_name)
    title = f"Overnight stay at {place_label}" if time_context["likely_overnight_sleep"] else f"Visited {place_label}"
    suggested_summary = (
        f"Stayed overnight at {place_label}."
        if time_context["likely_overnight_sleep"]
        else f"Spent {duration_label} at {place_label}."
    )
    reason = (
        f"Location samples show a {duration_label} stay with no blocking event. "
        f"{time_context['interpretation_hint']}"
    ).strip()
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
        "suggested_summary": suggested_summary,
        "suggested_contact_ids": contacts,
        "ignored_signature": segment.signature,
        "evidence": {
            "sample_count": len(segment.samples),
            "first_captured_at": segment.start_at.isoformat(),
            "last_captured_at": segment.samples[-1]["captured_at"].isoformat(),
            "source": "user_location_history",
            "duration_label": duration_label,
            "time_context": time_context,
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
            SELECT proposal_id
            FROM proposed_events
            WHERE user_email = %(user_email)s
              AND status <> 'expired'
              AND local_date = %(local_date)s
              AND start_at = %(start_at)s
              AND COALESCE(ignored_signature, '') = COALESCE(%(ignored_signature)s, '')
            LIMIT 1
            """,
            candidate,
        )
        if cur.fetchone():
            logger.info(
                "[proposed_events] proposal_duplicate user=%s date=%s start=%s end=%s place_id=%s signature=%s",
                candidate.get("user_email"),
                candidate.get("local_date"),
                _iso(candidate.get("start_at")),
                _iso(candidate.get("end_at")),
                candidate.get("place_id"),
                candidate.get("ignored_signature"),
            )
            conn.rollback()
            return None
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

    place_web_search = context["place_context"].get("web_search") if isinstance(context.get("place_context"), dict) else None
    place_web_results = place_web_search.get("results") if isinstance(place_web_search, dict) else []
    place_candidates = context["place_context"].get("place_candidates") if isinstance(context.get("place_context"), dict) else []
    has_place_evidence = bool(place_web_results or place_candidates)
    if not context["linked_contacts"] and not context["recent_events"] and not has_place_evidence:
        logger.info(
            "[proposed_events] enrichment_skipped_no_history place_id=%s place_name=%s",
            candidate.get("place_id"),
            candidate.get("place_name"),
        )
        candidate["evidence"]["place_intelligence"] = _place_intelligence_evidence(
            context=context,
            place_category="",
            place_summary="",
            proposed_place_name="",
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
                "You are a careful personal-memory analyst. Your job is to turn a location stay into a useful "
                "event proposal using only the evidence in the JSON payload. Distinguish observed facts from "
                "reasonable hypotheses. Use public place context only to understand what kind of venue this is; "
                "do not claim the user did anything unless the location, duration, timing, history, or contacts "
                "support it. Prefer specific, human event titles over generic 'Visited ...' titles when evidence "
                "supports a likely activity. If you have to return any time related information, also prefer human "
                "friendly ones (2 hours instead of 120 minutes for example). Return schema-valid JSON only."
            ),
            use_fast_model=False,
            reasoning_effort="xhigh",
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
        "place_context": _build_place_context(segment),
        "linked_contacts": linked_contacts,
        "recent_events": recent_events,
        "recurrence": recurrence,
    }


def _build_place_context(segment: StaySegment) -> dict[str, Any]:
    existing_place = places_service.get_place(segment.place_id) if segment.place_id else None
    place_snapshot = _place_snapshot(existing_place, segment)
    search_query = _place_search_query(place_snapshot)
    is_certain_known_place = bool(segment.place_id and segment.known_place_confidence == "high")
    google_candidates = []
    google_search = None
    if not is_certain_known_place:
        google_candidates, google_search = _lookup_google_place_candidates(segment)
    web_context = (
        _search_place_web_context(search_query)
        if is_certain_known_place and _should_search_place_context(place_snapshot)
        else None
    )
    return {
        "known_place": is_certain_known_place,
        "known_place_confidence": segment.known_place_confidence,
        "place": place_snapshot,
        "place_candidates": google_candidates,
        "google_search": google_search,
        "web_search": web_context,
    }


def _lookup_google_place_candidates(segment: StaySegment) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates_by_id: dict[str, dict[str, Any]] = {}
    query_points = _place_query_points(segment)
    cache_hits = 0
    live_queries = 0
    errors: list[str] = []
    for query_lat, query_lon in query_points:
        cached = google_place_cache.lookup_search(
            lat=query_lat,
            lon=query_lon,
            tolerance_m=google_place_cache.cache_tolerance_meters(),
        )
        if cached is not None:
            cache_hits += 1
            results = cached
        else:
            live_queries += 1
            response = google_places.search_nearby(
                lat=query_lat,
                lon=query_lon,
                radius_m=_place_query_radius_m(),
                max_results=_env_int("GOOGLE_PLACES_MAX_RESULTS", 10),
            )
            results = response.get("results") or []
            if not response.get("available"):
                error = str(response.get("error") or "unavailable")
                if error not in errors:
                    errors.append(error)
            if response.get("available"):
                google_place_cache.store_search(
                    center_lat=query_lat,
                    center_lon=query_lon,
                    radius_m=_place_query_radius_m(),
                    candidates=results,
                )
        for result in results:
            provider_place_id = str(result.get("provider_place_id") or "").strip()
            if not provider_place_id:
                continue
            distance = google_places.distance_to_candidate(segment.lat, segment.lon, result)
            normalized = dict(result)
            normalized["distance_m"] = round(distance, 1) if distance is not None else None
            normalized["fetched_at"] = _iso(result.get("fetched_at"))
            existing = candidates_by_id.get(provider_place_id)
            if existing is None or _candidate_sort_key(normalized) < _candidate_sort_key(existing):
                candidates_by_id[provider_place_id] = normalized

    candidates = sorted(candidates_by_id.values(), key=_candidate_sort_key)
    max_candidates = max(3, _env_int("GOOGLE_PLACES_MAX_CANDIDATES", 10))
    return candidates[:max_candidates], {
        "provider": "google",
        "query_points": [{"lat": lat, "lon": lon} for lat, lon in query_points],
        "cache_hits": cache_hits,
        "live_queries": live_queries,
        "errors": errors,
    }


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[float, str]:
    distance = _safe_optional_float(candidate.get("distance_m"))
    return (distance if distance is not None else float("inf"), str(candidate.get("title") or ""))


def _place_query_points(segment: StaySegment) -> list[tuple[float, float]]:
    points = [
        (float(sample["lat"]), float(sample["lon"]))
        for sample in segment.samples
        if _safe_optional_float(sample.get("lat")) is not None
        and _safe_optional_float(sample.get("lon")) is not None
    ]
    if not points:
        return [(segment.lat, segment.lon)]

    selected: list[tuple[float, float]] = [(segment.lat, segment.lon)]
    while len(selected) < max(1, _env_int("GOOGLE_PLACES_MAX_SEARCH_POINTS", PLACE_QUERY_MAX_POINTS)):
        next_point = max(
            points,
            key=lambda point: min(_distance_meters(point[0], point[1], chosen[0], chosen[1]) for chosen in selected),
        )
        if min(_distance_meters(next_point[0], next_point[1], chosen[0], chosen[1]) for chosen in selected) < 20.0:
            break
        selected.append(next_point)
    return selected


def _place_snapshot(existing_place: dict[str, Any] | None, segment: StaySegment) -> dict[str, Any]:
    source = existing_place or {}
    return {
        "place_id": source.get("place_id") or segment.place_id,
        "name": source.get("name") or segment.place_name,
        "aliases": source.get("aliases") or [],
        "description": source.get("description"),
        "address": source.get("address"),
        "city": source.get("city") or segment.city,
        "country": source.get("country") or segment.country,
        "lat": source.get("lat") if source.get("lat") is not None else segment.lat,
        "lon": source.get("lon") if source.get("lon") is not None else segment.lon,
    }


def _should_search_place_context(place: dict[str, Any]) -> bool:
    name = str(place.get("name") or "").strip()
    if not name:
        return False
    description = " ".join(str(place.get("description") or "").split()).strip()
    return len(description) < PLACE_DESCRIPTION_MIN_CHARS


def _place_search_query(place: dict[str, Any]) -> str:
    parts = [
        str(place.get("name") or "").strip(),
        str(place.get("city") or "").strip(),
        str(place.get("country") or "").strip(),
    ]
    query = " ".join(part for part in parts if part)
    if query:
        return query
    lat = place.get("lat")
    lon = place.get("lon")
    if lat is not None and lon is not None:
        return f"place near {float(lat):.5f}, {float(lon):.5f}"
    return ""


def _search_place_web_context(query: str) -> dict[str, Any] | None:
    if not query:
        return None
    try:
        result = web_tools.internet_search(query, max_results=PLACE_WEB_SEARCH_MAX_RESULTS)
    except Exception as exc:
        logger.warning("[proposed_events] place_web_search_failed query=%r error=%s", query, exc)
        return {"query": query, "results": [], "error": "search_failed"}

    if result.get("error"):
        error = result.get("error")
        code = error.get("code") if isinstance(error, dict) else "search_error"
        logger.info("[proposed_events] place_web_search_unavailable query=%r error=%s", query, code)
        return {"query": query, "results": [], "error": code}

    results: list[dict[str, Any]] = []
    for item in result.get("results") or []:
        if not isinstance(item, dict):
            continue
        title = " ".join(str(item.get("title") or "").split()).strip()
        url = " ".join(str(item.get("url") or "").split()).strip()
        snippet = " ".join(str(item.get("summary") or item.get("snippet") or "").split()).strip()
        if not any((title, url, snippet)):
            continue
        results.append(
            {
                "title": title[:160] or None,
                "url": url[:300] or None,
                "snippet": snippet[:500] or None,
            }
        )
    return {"query": query, "results": results[:PLACE_WEB_SEARCH_MAX_RESULTS]}


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
        "task": {
            "goal": "Create a reviewable proposed event from passive location evidence.",
            "audience": "The current user that generated the data.",
            "quality_bar": (
                "The proposal should feel like a thoughtful memory suggestion, not raw telemetry. "
                "Use a natural title and a summary that explains why this event is plausible."
            ),
        },
        "event_candidate": {
            "start_at": _iso(candidate.get("start_at")),
            "end_at": _iso(candidate.get("end_at")),
            "duration_minutes": candidate.get("duration_minutes"),
            "duration_label": (candidate.get("evidence") or {}).get("duration_label"),
            "time_context": (candidate.get("evidence") or {}).get("time_context"),
            "place_id": candidate.get("place_id"),
            "place_name": candidate.get("place_name"),
            "city": candidate.get("city"),
            "country": candidate.get("country"),
            "current_suggested_title": candidate.get("suggested_title"),
            "current_suggested_summary": candidate.get("suggested_summary"),
            "current_suggested_contact_ids": candidate.get("suggested_contact_ids"),
        },
        "place_context": context["place_context"],
        "linked_contacts_for_place": context["linked_contacts"],
        "recent_same_place_events": context["recent_events"],
        "recurrence": context["recurrence"],
        "decision_guidance": {
            "title": [
                "Prefer an activity-level title when the venue type, stay duration, timing, or history supports it.",
                "Examples of good shape: 'Lunch at Example Cafe', 'Visit to Example Clinic', 'Shopping at Example Market'.",
                "Use 'Visited <place>' only when the likely activity is unclear.",
            ],
            "summary": [
                "Write what likely happened, not why you believe it happened.",
                "Use natural time phrasing from duration_label; avoid raw minute phrasing like '75 minutes' unless under one hour.",
                "For whole-night stays, prefer a sleep/overnight-stay summary unless stronger evidence says otherwise.",
                "Do not overstate public web snippets as proof of what the user did.",
                "When evidence is weak, keep the summary generic and factual, such as an overnight stay or a visit at/near the place.",
            ],
            "reason": [
                "Explain why this proposal was generated and how confident the inference is.",
                "Mention location duration, timing, place context, recurrence, contacts, or lack of blocking calendar events as evidence.",
                "Keep reasoning out of suggested_summary so the user can edit the event notes without editing diagnostic text.",
            ],
            "people": [
                "Only return contact IDs present in linked_contacts_for_place or recent_same_place_events.people.",
                "Do not infer a person just because the venue name contains a personal name.",
            ],
            "confidence": [
                "Use high only when there is strong recurrence, linked-contact, same-place history, or a very clear venue/activity signal.",
                "Use medium when the proposal is plausible but mostly based on one stay and public place context.",
            ],
            "place": [
                "If public place context clarifies the venue, return a short neutral place_summary and place_category.",
                "For unknown places, select only a provider_place_id from place_context.place_candidates; use null when uncertain.",
                "Rank at most three supplied place candidate IDs for the review UI.",
                "Use the stay time range, duration, venue types, and candidate distance to assess likely activity, but do not invent a place.",
                "Do not include claims that are not supported by the provided place context, candidates, or web snippets.",
            ],
            "recurrence": "Use null recurrence_hint when history is too thin.",
        },
    }
    return json.dumps(prompt_payload, ensure_ascii=False, default=str)


def _apply_llm_enrichment(
    candidate: dict[str, Any],
    enriched: dict[str, Any],
    context: dict[str, Any],
    allowed_contact_ids: set[str],
) -> dict[str, Any]:
    title = " ".join(str(enriched.get("suggested_title") or "").split()).strip()
    summary = _normalize_generated_event_text(
        enriched.get("suggested_summary"),
        duration_minutes=candidate.get("duration_minutes"),
    )
    reason = _normalize_generated_event_text(
        enriched.get("reason"),
        duration_minutes=candidate.get("duration_minutes"),
    )
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
    place_category = " ".join(str(enriched.get("place_category") or "").split()).strip()
    place_summary = " ".join(str(enriched.get("place_summary") or "").split()).strip()
    place_context = context.get("place_context") if isinstance(context.get("place_context"), dict) else {}
    valid_candidate_ids = {
        str(item.get("provider_place_id") or "").strip()
        for item in (place_context.get("place_candidates") or [])
        if isinstance(item, dict) and str(item.get("provider_place_id") or "").strip()
    }
    selected_candidate_id = str(enriched.get("selected_place_candidate_id") or "").strip()
    if selected_candidate_id not in valid_candidate_ids:
        selected_candidate_id = ""
    ranked_candidate_ids = [
        candidate_id
        for candidate_id in (enriched.get("ranked_place_candidate_ids") or [])
        if str(candidate_id or "").strip() in valid_candidate_ids
    ]
    ranked_candidate_ids = list(dict.fromkeys(ranked_candidate_ids))[:3]
    proposed_place_name = ""
    candidate["evidence"]["llm_enrichment"] = {
        "used": True,
        "recurrence_hint": enriched.get("recurrence_hint"),
        "recent_event_ids": [event.get("id") for event in context["recent_events"]],
        "selected_place_candidate_id": selected_candidate_id or None,
        "ranked_place_candidate_ids": ranked_candidate_ids,
        "place_confidence": enriched.get("place_confidence"),
    }
    candidate["evidence"]["place_intelligence"] = _place_intelligence_evidence(
        context=context,
        place_category=place_category,
        place_summary=place_summary,
        proposed_place_name=proposed_place_name,
    )
    _maybe_update_known_place_description(candidate, context, place_summary)
    return candidate


def _normalize_generated_event_text(value: Any, *, duration_minutes: Any) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    try:
        minutes = int(duration_minutes)
    except (TypeError, ValueError):
        return text
    if minutes >= 60:
        text = re.sub(
            rf"\b{minutes}\s+minutes?\b",
            _humanize_duration_minutes(minutes),
            text,
            flags=re.IGNORECASE,
        )
    return text


def _place_intelligence_evidence(
    *,
    context: dict[str, Any],
    place_category: str,
    place_summary: str,
    proposed_place_name: str,
) -> dict[str, Any]:
    place_context = context.get("place_context") if isinstance(context.get("place_context"), dict) else {}
    web_search = place_context.get("web_search") if isinstance(place_context.get("web_search"), dict) else {}
    web_results = web_search.get("results") if isinstance(web_search, dict) else []
    source_urls = [
        str(item.get("url") or "").strip()
        for item in (web_results or [])
        if isinstance(item, dict) and str(item.get("url") or "").strip()
    ][:PLACE_WEB_SEARCH_MAX_RESULTS]
    return {
        "known_place": bool(place_context.get("known_place")),
        "web_search_query": web_search.get("query") if isinstance(web_search, dict) else None,
        "web_result_count": len(web_results or []) if isinstance(web_results, list) else 0,
        "source_urls": source_urls,
        "place_category": place_category or None,
        "place_summary": place_summary or None,
        "proposed_place_name": proposed_place_name or None,
        "candidates": place_context.get("place_candidates") or [],
        "google_search": place_context.get("google_search"),
    }


def _maybe_update_known_place_description(
    candidate: dict[str, Any],
    context: dict[str, Any],
    place_summary: str,
) -> None:
    place_id = str(candidate.get("place_id") or "").strip()
    if not place_id or not place_summary:
        return
    if len(place_summary) > PLACE_DESCRIPTION_MAX_CHARS:
        place_summary = place_summary[:PLACE_DESCRIPTION_MAX_CHARS].rsplit(" ", 1)[0].strip()
    if len(place_summary) < PLACE_DESCRIPTION_MIN_CHARS:
        return

    place_context = context.get("place_context") if isinstance(context.get("place_context"), dict) else {}
    if not place_context.get("known_place"):
        return

    place_intelligence = candidate["evidence"].get("place_intelligence")
    if not isinstance(place_intelligence, dict) or int(place_intelligence.get("web_result_count") or 0) <= 0:
        return

    try:
        updated = places_service.append_place_description_note(place_id, place_summary)
    except Exception as exc:
        logger.warning("[proposed_events] place_description_append_failed place_id=%s error=%s", place_id, exc)
        return
    if updated:
        candidate["evidence"].setdefault("place_intelligence", {})["description_appended"] = True


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
    candidate["evidence"]["place_intelligence"] = _place_intelligence_evidence(
        context=context,
        place_category="",
        place_summary="",
        proposed_place_name="",
    )
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
            SELECT id, lat, lon, accuracy_m, captured_at, source, timezone, place_name, city, country, updated_at
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


def _fetch_day_proposals(*, user_email: str, target_date: date) -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
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
              AND pe.local_date = %s
            ORDER BY pe.start_at ASC, pe.created_at ASC
            """,
            (user_email, target_date),
        )
        rows = [dict(row) for row in cur.fetchall()]
    return [_serialize_proposal(row) for row in rows]


def _enrich_location(row: dict[str, Any]) -> dict[str, Any]:
    matched = _nearest_known_place(float(row["lat"]), float(row["lon"]), row.get("accuracy_m"))
    row["known_place_match"] = None
    if matched:
        row["known_place_match"] = {
            "place_id": matched.get("place_id"),
            "name": matched.get("name"),
            "city": matched.get("city"),
            "country": matched.get("country"),
            "distance_m": matched.get("distance_m"),
            "confidence": matched.get("confidence"),
        }
        if matched.get("confidence") == "high":
            row.update(
                {
                    "place_id": matched.get("place_id"),
                    "place_name": matched.get("name") or row.get("place_name"),
                    "city": matched.get("city") or row.get("city"),
                    "country": matched.get("country") or row.get("country"),
                    "known_place_confidence": "high",
                }
            )
        else:
            row["place_id"] = None
            row["known_place_confidence"] = None
    else:
        row["place_id"] = None
        row["known_place_confidence"] = None
    return row


def _build_stay_segments(rows: list[dict[str, Any]], *, day_end: datetime) -> list[StaySegment]:
    if len(rows) < 2:
        return []
    segments: list[StaySegment] = []
    current: list[dict[str, Any]] = [rows[0]]
    for row in rows[1:]:
        if _same_stay_cluster(current, row):
            current.append(row)
            continue
        segments.append(_make_segment(current, next_start=row["captured_at"]))
        current = [row]
    segments.append(_make_segment(current, next_start=min(day_end, current[-1]["captured_at"])))
    return segments


def _same_stay_cluster(current: list[dict[str, Any]], row: dict[str, Any]) -> bool:
    current_place_ids = {
        str(item.get("place_id") or "").strip()
        for item in current
        if str(item.get("place_id") or "").strip()
    }
    row_place_id = str(row.get("place_id") or "").strip()
    if current_place_ids and row_place_id and row_place_id not in current_place_ids:
        return False
    if current_place_ids and row_place_id in current_place_ids:
        return True

    center_lat = sum(float(item["lat"]) for item in current) / len(current)
    center_lon = sum(float(item["lon"]) for item in current) / len(current)
    return _distance_meters(center_lat, center_lon, float(row["lat"]), float(row["lon"])) <= PLACE_CLUSTER_RADIUS_M


def _make_segment(rows: list[dict[str, Any]], *, next_start: datetime) -> StaySegment:
    first = rows[0]
    last = rows[-1]
    end_at = max(next_start, last["captured_at"])
    lat = sum(float(row["lat"]) for row in rows) / len(rows)
    lon = sum(float(row["lon"]) for row in rows) / len(rows)
    known_rows = [
        row
        for row in rows
        if row.get("place_id") and row.get("known_place_confidence") == "high"
    ]
    place_anchor = known_rows[0] if known_rows else first
    known_place_confidence = "high" if known_rows else None
    return StaySegment(
        start_at=first["captured_at"],
        end_at=end_at,
        samples=rows,
        place_id=place_anchor.get("place_id"),
        place_name=place_anchor.get("place_name"),
        city=place_anchor.get("city"),
        country=place_anchor.get("country"),
        lat=lat,
        lon=lon,
        signature=_location_signature(place_anchor),
        known_place_confidence=known_place_confidence,
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
    distance_m = nearest_distance
    conservative_accuracy = max(float(accuracy_m or 0.0), 20.0)
    confidence = (
        "high"
        if distance_m <= min(conservative_accuracy, threshold * 0.33)
        else "medium"
        if distance_m <= threshold * 0.66
        else "low"
    )
    nearest["distance_m"] = round(distance_m, 1)
    nearest["confidence"] = confidence
    return nearest


def _find_blocking_overlapping_events(
    *,
    segment: StaySegment,
    user_email: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    overlapping_events = _find_overlapping_events(
        start_at=segment.start_at,
        end_at=segment.end_at,
        user_email=user_email,
        limit=limit,
    )
    blocking_events: list[dict[str, Any]] = []
    for event in overlapping_events:
        decision = _event_blocks_location_segment(segment, event)
        event["overlap_decision"] = decision
        if decision["blocks_proposal"]:
            blocking_events.append(event)
    return blocking_events


def _find_overlapping_events(
    *,
    start_at: datetime,
    end_at: datetime,
    user_email: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    _ = user_email
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id,
                   e.title,
                   e.summary,
                   e.start_date,
                   e.end_date,
                   e.types,
                   e.tags,
                   e.place_id,
                   p.name AS place_name,
                   p.city AS place_city,
                   p.country AS place_country,
                   e.raw
            FROM events e
            LEFT JOIN places p ON p.place_id = e.place_id
            WHERE e.start_date < %s
              AND COALESCE(e.end_date, e.start_date + INTERVAL '30 minutes') > %s
            ORDER BY e.start_date ASC, e.id ASC
            LIMIT %s
            """,
            (end_at, start_at, max(1, limit)),
        )
        rows = [dict(row) for row in cur.fetchall()]
    return [_serialize_overlap_event(row) for row in rows if _event_blocks_location_gap(row)]


def _event_blocks_location_segment(segment: StaySegment, event: dict[str, Any]) -> dict[str, Any]:
    event_place_id = str(event.get("place_id") or "").strip()
    if event_place_id and segment.place_id and event_place_id == segment.place_id:
        return {
            "blocks_proposal": True,
            "confidence": "high",
            "reason": "The overlapping event is linked to the same known place.",
        }

    if _normalized_text_overlap(segment.place_name, event.get("title"), event.get("summary"), event.get("place_name")):
        return {
            "blocks_proposal": True,
            "confidence": "medium",
            "reason": "The overlapping event text references the same place or activity.",
        }

    try:
        decision = call_llm_json(
            _build_overlap_disambiguation_prompt(segment, event),
            system_prompt=(
                "You decide whether an existing calendar/memory event should block creating a "
                "location-derived proposed event. Return schema-valid JSON only."
            ),
            use_fast_model=False,
            reasoning_effort="xhigh",
            timeout=OVERLAP_DISAMBIGUATION_TIMEOUT_SECONDS,
            temperature=0.0,
            response_format=build_json_schema_response_format(
                name="proposed_event_overlap_decision",
                schema=PROPOSED_EVENT_OVERLAP_RESPONSE_SCHEMA,
            ),
        )
    except Exception as exc:
        logger.warning("[proposed_events] overlap_disambiguation_unavailable event_id=%s error=%s", event.get("id"), exc)
        return {
            "blocks_proposal": True,
            "confidence": "low",
            "reason": "Overlap disambiguation failed; conservatively treated the timed event as blocking.",
        }

    blocks = bool(decision.get("blocks_proposal"))
    confidence = str(decision.get("confidence") or "low").strip().lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"
    reason = " ".join(str(decision.get("reason") or "").split()).strip()
    return {
        "blocks_proposal": blocks,
        "confidence": confidence,
        "reason": reason or "LLM overlap disambiguation completed.",
    }


def _normalized_text_overlap(*values: Any) -> bool:
    normalized_values = [normalize_search_text(value or "") for value in values]
    first = normalized_values[0] if normalized_values else ""
    if not first or len(first) < 3:
        return False
    candidates = [value for value in normalized_values[1:] if value]
    return any(first in value or value in first for value in candidates)


def _build_overlap_disambiguation_prompt(segment: StaySegment, event: dict[str, Any]) -> str:
    payload = {
        "location_stay": {
            "start_at": segment.start_at.isoformat(),
            "end_at": segment.end_at.isoformat(),
            "duration_minutes": int((segment.end_at - segment.start_at).total_seconds() // 60),
            "place_id": segment.place_id,
            "place_name": segment.place_name,
            "city": segment.city,
            "country": segment.country,
            "signature": segment.signature,
        },
        "overlapping_event": {
            "id": event.get("id"),
            "title": event.get("title"),
            "summary": event.get("summary"),
            "start_at": event.get("start_at"),
            "end_at": event.get("end_at"),
            "place_id": event.get("place_id"),
            "place_name": event.get("place_name"),
            "city": event.get("place_city"),
            "country": event.get("place_country"),
            "types": event.get("types") or [],
            "tags": event.get("tags") or [],
            "source": event.get("source"),
        },
        "decision_rules": [
            "Return blocks_proposal=true only if the overlapping event likely represents the same real-world activity as the location stay.",
            "Unrelated broad calendar context, travel area labels, reminders, holidays, and vague all-day style labels should not block.",
            "If the location stay is a specific venue and the event title is only a broad region or unrelated topic, return false.",
            "Prefer false when the evidence does not connect the event to the stay's place/activity.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def _event_blocks_location_gap(row: dict[str, Any]) -> bool:
    raw = row.get("raw")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    if isinstance(raw, dict):
        all_day_value = raw.get("all_day") or raw.get("is_all_day") or raw.get("allDay")
        if all_day_value is True or str(all_day_value).strip().lower() == "true":
            return False

    start_at = _parse_datetime(row.get("start_date"))
    end_at = _parse_datetime(row.get("end_date"))
    if start_at and end_at:
        duration_hours = (end_at - start_at).total_seconds() / 3600
        if duration_hours >= ALL_DAY_EVENT_MIN_HOURS:
            return False
    return True


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


def _humanize_duration_minutes(minutes: int) -> str:
    clean_minutes = max(0, int(minutes))
    if clean_minutes < 60:
        return f"{clean_minutes} minute{'s' if clean_minutes != 1 else ''}"

    hours = clean_minutes // 60
    remainder = clean_minutes % 60
    if remainder == 0:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    if remainder <= 5:
        return f"about {hours} hour{'s' if hours != 1 else ''}"
    if remainder < 15:
        return f"a bit more than {hours} hour{'s' if hours != 1 else ''}"
    if remainder == 15:
        return f"{hours} hour{'s' if hours != 1 else ''} and 15 minutes"
    if remainder < 30:
        return f"about {hours} and a half hours"
    if remainder == 30:
        return f"{hours} and a half hour{'s' if hours != 1 else ''}"
    if remainder < 45:
        return f"more than {hours} and a half hour{'s' if hours != 1 else ''}"
    if remainder == 45:
        return f"{hours} hour{'s' if hours != 1 else ''} and 45 minutes"
    return f"almost {hours + 1} hours"


def _build_time_context(start_at: datetime, end_at: datetime, timezone_name: str | None) -> dict[str, Any]:
    tz = _resolve_timezone(timezone_name)
    local_start = start_at.astimezone(tz)
    local_end = end_at.astimezone(tz)
    duration_minutes = int((end_at - start_at).total_seconds() // 60)
    spans_midnight = local_start.date() != local_end.date()
    starts_evening_or_night = local_start.hour >= 20 or local_start.hour <= 4
    starts_after_midnight = 0 <= local_start.hour <= 2
    ends_morning = 4 <= local_end.hour <= 11
    day_window_clipped_overnight = duration_minutes >= 240 and starts_after_midnight and ends_morning
    likely_overnight_sleep = duration_minutes >= 240 and ends_morning and (
        (spans_midnight and starts_evening_or_night) or day_window_clipped_overnight
    )
    if likely_overnight_sleep:
        hint = (
            "Because this covers the overnight sleep window into morning, treat it as an overnight stay "
            "or sleep unless other evidence says otherwise."
        )
    elif local_start.hour in {11, 12, 13, 14} and 30 <= duration_minutes <= 150:
        hint = "Midday timing and duration may fit lunch or a daytime visit when venue context supports it."
    elif local_start.hour in {18, 19, 20, 21} and 45 <= duration_minutes <= 240:
        hint = "Evening timing and duration may fit dinner or an evening visit when venue context supports it."
    else:
        hint = "Use the timing as context, but do not over-infer the activity."
    return {
        "local_start": local_start.isoformat(),
        "local_end": local_end.isoformat(),
        "duration_label": _humanize_duration_minutes(duration_minutes),
        "spans_midnight": spans_midnight,
        "day_window_clipped_overnight": day_window_clipped_overnight,
        "likely_overnight_sleep": likely_overnight_sleep,
        "interpretation_hint": hint,
    }


def _serialize_location(row: dict[str, Any]) -> dict[str, Any]:
    output = dict(row)
    for key in ("captured_at", "updated_at"):
        value = output.get(key)
        if hasattr(value, "isoformat"):
            output[key] = value.isoformat()
    for key in ("lat", "lon", "accuracy_m"):
        if output.get(key) is not None:
            output[key] = float(output[key])
    return output


def _serialize_overlap_event(row: dict[str, Any]) -> dict[str, Any]:
    output = {
        "id": row.get("id"),
        "title": row.get("title"),
        "summary": row.get("summary"),
        "start_at": _iso(row.get("start_date")),
        "end_at": _iso(row.get("end_date")),
        "place_id": row.get("place_id"),
        "place_name": row.get("place_name"),
        "place_city": row.get("place_city"),
        "place_country": row.get("place_country"),
        "types": row.get("types") or [],
        "tags": row.get("tags") or [],
    }
    raw = row.get("raw")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    if isinstance(raw, dict):
        output["source"] = raw.get("source")
        output["external_id"] = raw.get("external_id")
    return output


def _serialize_timeline_segment(
    segment: StaySegment,
    *,
    ignores: set[tuple[str, str]],
    user_email: str,
) -> dict[str, Any]:
    skip_reason = _segment_skip_reason(segment, ignores)
    would_propose = skip_reason == "candidate_filtered"
    overlapping_events: list[dict[str, Any]] = []
    overlaps_event = False
    if would_propose:
        overlapping_events = _find_blocking_overlapping_events(
            segment=segment,
            user_email=user_email,
        )
        overlaps_event = bool(overlapping_events)
        skip_reason = "overlapping_event" if overlaps_event else "eligible_candidate"
        would_propose = not overlaps_event
    duration_minutes = int((segment.end_at - segment.start_at).total_seconds() // 60)
    return {
        "start_at": segment.start_at.isoformat(),
        "end_at": segment.end_at.isoformat(),
        "duration_minutes": duration_minutes,
        "sample_count": len(segment.samples),
        "place_id": segment.place_id,
        "place_name": segment.place_name,
        "city": segment.city,
        "country": segment.country,
        "lat": segment.lat,
        "lon": segment.lon,
        "signature": segment.signature,
        "overlaps_event": overlaps_event,
        "overlapping_events": overlapping_events,
        "skip_reason": skip_reason,
        "would_propose": would_propose,
        "first_sample_id": segment.samples[0].get("id") if segment.samples else None,
        "last_sample_id": segment.samples[-1].get("id") if segment.samples else None,
    }


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


def _safe_optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_place_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:48] or "place"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _place_query_radius_m() -> float:
    try:
        return max(25.0, float(os.getenv("GOOGLE_PLACES_SEARCH_RADIUS_M", PLACE_QUERY_RADIUS_M)))
    except (TypeError, ValueError):
        return PLACE_QUERY_RADIUS_M


def _serialize_proposal(row: dict[str, Any]) -> dict[str, Any]:
    output = dict(row)
    for key in ("local_date", "start_at", "end_at", "expires_at", "created_at", "updated_at"):
        value = output.get(key)
        if hasattr(value, "isoformat"):
            output[key] = value.isoformat()
    if output.get("canonical_place_name") and not output.get("place_name"):
        output["place_name"] = output["canonical_place_name"]
    duration_minutes = output.get("duration_minutes")
    if duration_minutes is not None:
        output["duration_label"] = _humanize_duration_minutes(int(duration_minutes))
    evidence = output.get("evidence")
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            evidence = {}
    if isinstance(evidence, dict):
        place_intelligence = evidence.get("place_intelligence")
        if isinstance(place_intelligence, dict):
            candidates = [item for item in (place_intelligence.get("candidates") or []) if isinstance(item, dict)]
            llm_enrichment = evidence.get("llm_enrichment")
            ranked_ids = (
                llm_enrichment.get("ranked_place_candidate_ids")
                if isinstance(llm_enrichment, dict)
                else []
            )
            by_id = {
                str(item.get("provider_place_id") or "").strip(): item
                for item in candidates
                if str(item.get("provider_place_id") or "").strip()
            }
            ordered = [by_id[provider_id] for provider_id in ranked_ids if provider_id in by_id]
            ordered.extend(item for item in candidates if item not in ordered)
            output["place_candidates"] = ordered[:3]
    return output
