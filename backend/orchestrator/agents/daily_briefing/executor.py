from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from math import ceil
from time import perf_counter
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import daily_briefings
import news_feeds
import news_personalization
import retrieval
import todos as todos_service
from agent.tool_loop_runner import run_profiled_tool_loop
from agents.daily_briefing.profile import (
    build_event_research_profile,
)
from agents.daily_briefing.validators import (
    validate_event_sections,
    validate_news_section,
    validate_summary,
)
from db import get_conn
from llm_helpers import call_llm, call_llm_json
from search_normalization import normalize_search_text

logger = logging.getLogger(__name__)

DEFAULT_SIMILAR_LIMIT = 4
ATTENDEE_OVERLAP_THRESHOLD = 0.8
ATTENDEE_OVERLAP_CANDIDATE_MULTIPLIER = 4
BIRTHDAY_LOOKAHEAD_DAYS = 7
MAX_NEWS_SELECTED_ARTICLES = 24
NEWS_TOPIC_HARD_CAP = 10
NEWS_GENERAL_MIN_SELECTED = 3
NEWS_TOPIC_MIN_SCORE = 3.0
NEWS_GENERAL_MIN_SCORE = 2.5
NEWS_SOURCE_REPEAT_PENALTY = 0.8
NEWS_TOPIC_REPEAT_PENALTY = 0.35
NEWS_SUMMARY_MAX_WORKERS = 4
EVENT_ENRICHMENT_MAX_WORKERS = 4
EVENT_SUMMARY_MAX_WORKERS = 3
BRIEFING_SECTION_MAX_WORKERS = 2
_NEWS_SOURCE_QUALITY: dict[str, int] = {
    "reuters": 4,
    "bbc_world": 4,
    "nytimes": 4,
    "bloomberg": 4,
    "wsj": 4,
    "techcrunch": 3,
    "hacker_news": 2,
    "tavily": 1,
}
_NEWS_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "into",
    "your",
    "today",
    "news",
    "meeting",
    "work",
    "update",
    "project",
}

_LOW_VALUE_PREP_PATTERNS = [
    re.compile(r"\b(review|read)\b.*\b(notes?|agenda)\b", re.IGNORECASE),
    re.compile(r"\bconfirm\b.*\bagenda\b", re.IGNORECASE),
    re.compile(r"\bprepare\b.*\b(talking points?|questions?)\b", re.IGNORECASE),
    re.compile(r"\badd\b.*\b(calendar|agenda)\b", re.IGNORECASE),
    re.compile(r"\bcheck\b.*\b(calendar|invite|time)\b", re.IGNORECASE),
]

_EVIDENCE_MARKER_RE = re.compile(
    r"\(\s*evidence\s*:\s*(history|todo|current_notes|research:[^)]+|https?://[^)\s]+)\s*\)$",
    re.IGNORECASE,
)


def _get_daily_briefing_user_context(user_email: str | None, query: str) -> str:
    """Build user identity + facts context for daily briefing prompts."""
    if not user_email:
        return ""
    from prompts.context import get_self_context, get_user_facts_context

    parts: list[str] = []
    self_ctx = get_self_context(user_email)
    if self_ctx:
        parts.append(self_ctx)

    facts_ctx = get_user_facts_context(user_email, query)
    if facts_ctx:
        parts.append(facts_ctx)

    if not parts:
        return ""
    return "\n\n".join(parts)


def handle_daily_briefing_request(payload: dict[str, Any]) -> dict[str, Any]:
    date_value = payload.get("date")
    timezone_name = payload.get("timezone")
    user_email = payload.get("user_email")
    if not date_value or not timezone_name:
        return {
            "status": "error",
            "message": "date and timezone are required",
        }
    try:
        return build_daily_briefing(
            date_value=str(date_value),
            timezone_name=str(timezone_name),
            user_email=str(user_email) if user_email else "default_user",
        )
    except ValueError as exc:
        return {
            "status": "error",
            "message": str(exc),
        }


def build_daily_briefing(
    *,
    date_value: str,
    timezone_name: str,
    user_email: str | None = None,
) -> dict[str, Any]:
    pipeline_start = perf_counter()
    local_date = _parse_date(date_value)
    tz = _resolve_timezone(timezone_name)
    start_local = datetime.combine(local_date, time.min).replace(tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    logger.info(
        "[briefing] Starting pipeline for %s (%s), user=%s",
        local_date.isoformat(),
        timezone_name,
        user_email or "default_user",
    )

    # -- 1. Gather raw event data ------------------------------------------------
    t0 = perf_counter()
    from contacts import get_self_contact_id

    events = [_apply_timezone(event, tz) for event in _fetch_events_for_span(start_utc, end_utc)]
    self_contact_id = get_self_contact_id(user_email) if user_email else None
    event_contexts = _build_event_contexts_parallel(
        events,
        tz,
        start_utc,
        self_contact_id=self_contact_id,
    )
    logger.info(
        "[briefing] Events: %d found, %d with similar history (%.0fms)",
        len(events),
        sum(1 for ec in event_contexts if ec.get("similar_events")),
        (perf_counter() - t0) * 1000,
    )
    for ec in event_contexts:
        logger.info(
            "[briefing]   - %s | %s | %d people, %d todos, %d similar",
            ec.get("local_start", "?"),
            ec.get("title", "Untitled"),
            len(ec.get("contacts") or []),
            len(ec.get("todos") or []),
            len(ec.get("similar_events") or []),
        )

    # -- 2. Per-event deep analysis (parallel dedicated LLM call each) -----------
    t0 = perf_counter()
    event_contexts = _summarize_events_parallel(
        event_contexts,
        timezone_name,
        user_email=user_email,
    )
    logger.info(
        "[briefing] Deep analysis: %d event(s) analyzed (%.0fms)",
        len(event_contexts),
        (perf_counter() - t0) * 1000,
    )

    # -- 3-5. Fetch birthdays, news, and unlinked todos in parallel --------------
    t0 = perf_counter()
    with ThreadPoolExecutor(max_workers=3) as pool:
        birthdays_future = pool.submit(_fetch_upcoming_birthdays, local_date)
        news_future = pool.submit(_fetch_news_safely)
        todos_future = pool.submit(todos_service.list_unlinked_relevant_todos, pending_only=True)

        upcoming_birthdays = birthdays_future.result()
        news_articles = news_future.result()
        all_todos = todos_future.result()

    logger.info(
        "[briefing] Parallel background fetches complete (%.0fms)", (perf_counter() - t0) * 1000
    )

    if upcoming_birthdays:
        bday_parts = []
        for b in upcoming_birthdays:
            name = b["display_name"]
            label = "today" if b.get("is_today") else f"{b.get('days_away')}d"
            bday_parts.append(f"{name} ({label})")
        logger.info(
            "[briefing] Birthdays: %d upcoming (%s)",
            len(upcoming_birthdays),
            ", ".join(bday_parts),
        )
    else:
        logger.info("[briefing] Birthdays: none in next %d days", BIRTHDAY_LOOKAHEAD_DAYS)

    topic_matched_count = sum(1 for a in news_articles if a.get("topic_matches"))
    logger.info(
        "[briefing] News: %d article(s) (%d topic-matched, %d general)",
        len(news_articles),
        topic_matched_count,
        len(news_articles) - topic_matched_count,
    )

    logger.info("[briefing] Unlinked todos: %d", len(all_todos))

    weather_summary = _build_weather_summary(
        local_date=local_date,
        timezone_name=timezone_name,
        user_email=user_email,
    )

    context = {
        "date": local_date.isoformat(),
        "timezone": timezone_name,
        "user_email": user_email,
        "day_start": start_local.isoformat(),
        "day_end": end_local.isoformat(),
        "events": event_contexts,
        "all_todos": all_todos,
        "upcoming_birthdays": upcoming_birthdays,
        "news_articles": news_articles,
        "weather_summary": weather_summary,
    }

    selected_news = (
        _select_news_for_generation(context)
        if news_articles
        else {"topic_articles": [], "general_articles": []}
    )
    if news_articles:
        selected_news = _enrich_selected_news_summaries(
            selected_news,
            user_email=user_email,
        )
    context["selected_news"] = selected_news

    # -- 6. Assemble final markdown & summary ------------------------------------
    t0 = perf_counter()
    markdown = _generate_markdown(context, selected_news=selected_news, user_email=user_email)
    logger.info(
        "[briefing] Markdown generated: %d chars (%.0fms)",
        len(markdown),
        (perf_counter() - t0) * 1000,
    )

    t0 = perf_counter()
    summary = _generate_summary(context, markdown, user_email=user_email)
    logger.info("[briefing] Summary generated (%.0fms)", (perf_counter() - t0) * 1000)

    todo_count = _count_todos(event_contexts, all_todos)
    stored = daily_briefings.upsert_daily_briefing(
        user_email=user_email,
        briefing_date=local_date,
        timezone=timezone_name,
        markdown=markdown,
        summary=summary,
        event_count=len(events),
        todo_count=todo_count,
    )
    news_items = _build_briefing_news_items(selected_news)
    persisted_news_items = daily_briefings.replace_daily_briefing_news_items(
        briefing_id=str(stored.get("briefing_id") or ""),
        user_email=user_email,
        briefing_date=local_date,
        timezone=timezone_name,
        items=news_items,
    )

    total_ms = (perf_counter() - pipeline_start) * 1000
    logger.info(
        "[briefing] Pipeline complete: %d event(s), %d todo(s), %d news, %.1fs total",
        len(events),
        todo_count,
        len(news_articles),
        total_ms / 1000,
    )

    return {
        "briefing_id": stored.get("briefing_id"),
        "date": local_date.isoformat(),
        "timezone": timezone_name,
        "event_count": len(events),
        "todo_count": todo_count,
        "summary": summary,
        "markdown": markdown,
        "news_items": persisted_news_items,
    }


def _parse_date(value: str) -> date:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError("date is required")
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        try:
            return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).date()
        except ValueError as exc:
            raise ValueError(f"Invalid date: {value}") from exc


def _resolve_timezone(name: str) -> ZoneInfo:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("timezone is required")
    try:
        return ZoneInfo(cleaned)
    except Exception as exc:
        raise ValueError(f"Invalid timezone: {name}") from exc


def _fetch_events_for_span(start: datetime, end: datetime) -> list[dict[str, Any]]:
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
            WHERE e.start_date >= %s AND e.start_date < %s
            ORDER BY e.start_date ASC
            """,
            (start, end),
        )
        rows = [dict(row) for row in cur.fetchall()]
        event_ids = [r["id"] for r in rows]
        people_map = fetch_event_people(cur, event_ids)
        for r in rows:
            r["people"] = people_map.get(r["id"], [])
    return [_normalize_event_row(row) for row in rows]


def _normalize_event_row(row: dict[str, Any]) -> dict[str, Any]:
    raw_data = row.get("raw") or {}
    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data)
        except json.JSONDecodeError:
            raw_data = {"raw": raw_data}
    start_value = row.get("start_date")
    end_value = row.get("end_date")
    place_id = row.get("place_id")
    return {
        "id": row.get("id"),
        "start_date": _isoformat(start_value),
        "end_date": _isoformat(end_value),
        "people": row.get("people") or [],
        "tags": row.get("tags") or [],
        "types": row.get("types") or [],
        "title": row.get("title") or "",
        "summary": row.get("summary") or "",
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


def _apply_timezone(event: dict[str, Any], tz: ZoneInfo) -> dict[str, Any]:
    start_value = _parse_datetime(event.get("start_date"))
    end_value = _parse_datetime(event.get("end_date"))
    return {
        **event,
        "local_start": _format_local(start_value, tz),
        "local_end": _format_local(end_value, tz),
    }


def _build_event_contexts_parallel(
    events: list[dict[str, Any]],
    tz: ZoneInfo,
    start_utc: datetime,
    *,
    self_contact_id: str | None = None,
) -> list[dict[str, Any]]:
    if not events:
        return []

    contexts: list[dict[str, Any] | None] = [None] * len(events)
    max_workers = min(EVENT_ENRICHMENT_MAX_WORKERS, len(events))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(_build_single_event_context, event, tz, start_utc, self_contact_id): idx
            for idx, event in enumerate(events)
        }
        for future in as_completed(future_map):
            idx = future_map[future]
            event = events[idx]
            try:
                contexts[idx] = future.result()
            except Exception:
                logger.warning(
                    "[briefing] Failed to enrich event '%s', using minimal context",
                    event.get("title") or "Untitled",
                    exc_info=True,
                )
                contexts[idx] = {
                    **event,
                    "similar_events": [],
                    "todos": [],
                    "related_todos": [],
                    "contacts": [],
                }
    return [ctx for ctx in contexts if ctx is not None]


def _build_single_event_context(
    event: dict[str, Any],
    tz: ZoneInfo,
    start_utc: datetime,
    self_contact_id: str | None,
) -> dict[str, Any]:
    similar_events = [
        _apply_timezone(similar, tz)
        for similar in _fetch_similar_events(
            event,
            start_utc,
            DEFAULT_SIMILAR_LIMIT,
            self_contact_id=self_contact_id,
        )
    ]
    event_todos = todos_service.list_event_todos(event["id"], pending_only=True)
    related_todos = _collect_related_event_todos(similar_events)
    contacts = _fetch_contact_summaries(event.get("people") or [])
    return {
        **event,
        "similar_events": similar_events,
        "todos": event_todos,
        "related_todos": related_todos,
        "contacts": contacts,
    }


def _summarize_events_parallel(
    event_contexts: list[dict[str, Any]],
    timezone_name: str,
    *,
    user_email: str | None = None,
) -> list[dict[str, Any]]:
    if not event_contexts:
        return []

    max_workers = min(EVENT_SUMMARY_MAX_WORKERS, len(event_contexts))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(_summarize_event, ec, timezone_name, user_email=user_email): idx
            for idx, ec in enumerate(event_contexts)
        }
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                event_contexts[idx]["deep_summary"] = future.result()
            except Exception:
                logger.warning(
                    "[briefing] Failed deep analysis for '%s'",
                    event_contexts[idx].get("title") or "Untitled",
                    exc_info=True,
                )
                event_contexts[idx]["deep_summary"] = ""
    return event_contexts


def _fetch_news_safely() -> list[dict[str, Any]]:
    try:
        return news_feeds.fetch_news()
    except Exception:
        logger.warning("News feed aggregation failed, continuing without news", exc_info=True)
        return []


def _fetch_similar_events(
    event: dict[str, Any],
    day_start: datetime,
    limit: int,
    *,
    self_contact_id: str | None = None,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    event_id = str(event.get("id") or "").strip()
    if not event_id:
        return []

    matches: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    title = (event.get("title") or "").strip()
    if title:
        title_matches = _fetch_similar_by_title(event_id, title, day_start, limit)
        for row in title_matches:
            rid = str(row.get("id") or "").strip()
            if not rid or rid in seen_ids:
                continue
            seen_ids.add(rid)
            row["similarity_match_type"] = "title_exact"
            matches.append(row)

    if len(matches) >= limit:
        return matches

    remaining = limit - len(matches)
    recurrence_key = _extract_recurrence_key(event)
    if recurrence_key and remaining > 0:
        recurrence_matches = _fetch_similar_by_recurrence(
            event_id,
            recurrence_key,
            day_start,
            remaining,
        )
        for row in recurrence_matches:
            rid = str(row.get("id") or "").strip()
            if not rid or rid in seen_ids:
                continue
            seen_ids.add(rid)
            row["similarity_match_type"] = "recurrence"
            matches.append(row)
            if len(matches) >= limit:
                return matches

    remaining = limit - len(matches)
    if remaining <= 0:
        return matches

    if not _normalize_similarity_attendees(event.get("people") or [], self_contact_id):
        return matches

    attendee_matches = _fetch_similar_by_attendee_overlap(
        event,
        day_start,
        limit=max(remaining * ATTENDEE_OVERLAP_CANDIDATE_MULTIPLIER, remaining),
        self_contact_id=self_contact_id,
    )
    for row in attendee_matches:
        rid = str(row.get("id") or "").strip()
        if not rid or rid in seen_ids:
            continue
        seen_ids.add(rid)
        matches.append(row)
        if len(matches) >= limit:
            break
    return matches


def _fetch_similar_by_attendee_overlap(
    event: dict[str, Any],
    day_start: datetime,
    limit: int,
    *,
    self_contact_id: str | None = None,
) -> list[dict[str, Any]]:
    current_set = _normalize_similarity_attendees(event.get("people") or [], self_contact_id)
    attendee_count = len(current_set)
    event_id = str(event.get("id") or "").strip()
    if not event_id or attendee_count == 0 or limit <= 0:
        return []

    min_overlap = max(1, ceil(attendee_count * ATTENDEE_OVERLAP_THRESHOLD))

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
              p.lon,
              COUNT(DISTINCT CASE WHEN ec.contact_id = ANY(%s) THEN ec.contact_id END) AS overlap_count
            FROM events AS e
            LEFT JOIN places AS p ON p.place_id = e.place_id
            LEFT JOIN event_contacts ec ON ec.event_id = e.id
            WHERE e.id <> %s
              AND e.start_date < %s
            GROUP BY
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
              p.name,
              p.city,
              p.country,
              p.lat,
              p.lon
            HAVING COUNT(DISTINCT CASE WHEN ec.contact_id = ANY(%s) THEN ec.contact_id END) >= %s
            ORDER BY overlap_count DESC, e.start_date DESC
            LIMIT %s
            """,
            (list(current_set), event_id, day_start, list(current_set), min_overlap, limit),
        )
        rows = [dict(row) for row in cur.fetchall()]
        event_ids = [r["id"] for r in rows]
        people_map = fetch_event_people(cur, event_ids)
        for r in rows:
            r["people"] = people_map.get(r["id"], [])

    scored_matches: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        overlap_count = int(row.get("overlap_count") or 0)
        normalized_row = _normalize_event_row(row)
        candidate_people = _normalize_similarity_attendees(
            normalized_row.get("people") or [],
            self_contact_id,
        )
        if not candidate_people:
            continue
        intersection_count = len(current_set & candidate_people)
        overlap_ratio = intersection_count / attendee_count if attendee_count else 0.0
        if overlap_ratio < ATTENDEE_OVERLAP_THRESHOLD and not (
            attendee_count == len(candidate_people) == intersection_count
        ):
            continue

        is_exact = attendee_count == len(candidate_people) == intersection_count
        normalized_row["attendee_overlap_ratio"] = round(overlap_ratio, 3)
        normalized_row["attendee_overlap_count"] = overlap_count
        normalized_row["similarity_match_type"] = "attendee_exact" if is_exact else "attendee_overlap"
        score = overlap_ratio + (0.15 if is_exact else 0.0)
        scored_matches.append((score, normalized_row))

    scored_matches.sort(
        key=lambda item: (
            item[0],
            str(item[1].get("start_date") or ""),
        ),
        reverse=True,
    )
    return [row for _score, row in scored_matches]


def _normalize_similarity_attendees(
    attendees: list[Any],
    self_contact_id: str | None,
) -> set[str]:
    normalized = {str(contact_id).strip() for contact_id in attendees if str(contact_id).strip()}
    self_id = str(self_contact_id or "").strip()
    if self_id:
        normalized.discard(self_id)
    return normalized


def _fetch_similar_by_title(
    event_id: str,
    title: str,
    day_start: datetime,
    limit: int,
) -> list[dict[str, Any]]:
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
            WHERE e.id <> %s
              AND e.title IS NOT NULL
              AND lower(e.title) = lower(%s)
              AND e.start_date < %s
            ORDER BY e.start_date DESC
            LIMIT %s
            """,
            (event_id, title, day_start, limit),
        )
        rows = [dict(row) for row in cur.fetchall()]
        event_ids = [r["id"] for r in rows]
        people_map = fetch_event_people(cur, event_ids)
        for r in rows:
            r["people"] = people_map.get(r["id"], [])
    return [_normalize_event_row(row) for row in rows]


def _fetch_similar_by_recurrence(
    event_id: str,
    recurrence_key: str,
    day_start: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    if not recurrence_key:
        return []
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
            WHERE e.id <> %s
              AND e.start_date < %s
              AND (
                e.raw->>'recurringEventId' = %s
                OR e.raw->>'recurrenceId' = %s
                OR e.raw->>'recurrence_id' = %s
                OR e.raw->>'recurring_event_id' = %s
              )
            ORDER BY e.start_date DESC
            LIMIT %s
            """,
            (
                event_id,
                day_start,
                recurrence_key,
                recurrence_key,
                recurrence_key,
                recurrence_key,
                limit,
            ),
        )
        rows = [dict(row) for row in cur.fetchall()]
        event_ids = [r["id"] for r in rows]
        people_map = fetch_event_people(cur, event_ids)
        for r in rows:
            r["people"] = people_map.get(r["id"], [])
    return [_normalize_event_row(row) for row in rows]


def _extract_recurrence_key(event: dict[str, Any]) -> str | None:
    raw_data = event.get("raw") or {}
    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data)
        except json.JSONDecodeError:
            raw_data = {}
    for key in (
        "recurringEventId",
        "recurrenceId",
        "recurrence_id",
        "recurring_event_id",
    ):
        value = raw_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _fetch_contact_summaries(contact_ids: list[str]) -> list[dict[str, Any]]:
    summaries = retrieval.fetch_contact_summaries(contact_ids)
    return [summaries[cid] for cid in contact_ids if cid in summaries]


def _fetch_upcoming_birthdays(
    local_date: date,
    lookahead_days: int = BIRTHDAY_LOOKAHEAD_DAYS,
) -> list[dict[str, Any]]:
    """Fetch contacts whose birthday falls within the next *lookahead_days* days.

    The query compares month/day only so it works across year boundaries
    (e.g. Dec 29 with a 7-day window wraps into Jan).
    """
    dates_to_check: list[tuple[int, int]] = []
    for offset in range(lookahead_days + 1):
        d = local_date + timedelta(days=offset)
        dates_to_check.append((d.month, d.day))

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, birthday
            FROM contacts
            WHERE birthday IS NOT NULL
              AND (EXTRACT(MONTH FROM birthday), EXTRACT(DAY FROM birthday))
                  IN (SELECT unnest(%s::int[]), unnest(%s::int[]))
            ORDER BY display_name
            """,
            (
                [m for m, _ in dates_to_check],
                [d for _, d in dates_to_check],
            ),
        )
        rows = [dict(row) for row in cur.fetchall()]

    results: list[dict[str, Any]] = []
    for row in rows:
        bday: date = row["birthday"]
        bday_this_year = bday.replace(year=local_date.year)
        # handle year wrap (birthday in early Jan when today is late Dec)
        if bday_this_year < local_date:
            bday_this_year = bday.replace(year=local_date.year + 1)
        days_away = (bday_this_year - local_date).days
        results.append(
            {
                "contact_id": row["contact_id"],
                "display_name": row["display_name"],
                "birthday": bday.isoformat(),
                "days_away": days_away,
                "is_today": days_away == 0,
            }
        )
    results.sort(key=lambda r: r["days_away"])
    return results


def _summarize_event(
    event_context: dict[str, Any], timezone_name: str, *, user_email: str | None = None
) -> str:
    """Produce a focused summary for a single event.

    Two phases:
    1. **Research** – a lightweight tool loop with ``web_search`` and
       ``fetch_web_page``.  The LLM decides whether external research would
       help (company background, public agenda, venue info, etc.) or skips
       tool use for routine/internal events.
    2. **Synthesis** – a plain ``call_llm`` that combines the event data with
       any research findings into a structured preparation summary.

    Using a per-event call avoids the "wall of text" problem where the model
    has to juggle many events at once and produces shallow output.
    """
    title = event_context.get("title") or "Untitled"
    event_text = _format_event_for_analysis(event_context)
    logger.info("[briefing.event] Analyzing: '%s'", title)

    # -- Phase 1: optional web research via tool loop -------------------------
    t0 = perf_counter()
    research_notes = _research_event(
        event_text,
        title,
        timezone_name,
        event_context=event_context,
        user_email=user_email,
    )
    if research_notes:
        logger.info(
            "[briefing.event] Research for '%s': %d chars (%.0fms)",
            title,
            len(research_notes),
            (perf_counter() - t0) * 1000,
        )
    else:
        logger.info(
            "[briefing.event] Research for '%s': skipped/none (%.0fms)",
            title,
            (perf_counter() - t0) * 1000,
        )

    # -- Phase 2: synthesise everything into a structured summary -------------
    t0 = perf_counter()
    summary = _synthesise_event_summary(
        event_text, research_notes, title, timezone_name, user_email=user_email
    )
    logger.info(
        "[briefing.event] Synthesis for '%s': %d chars (%.0fms)",
        title,
        len(summary),
        (perf_counter() - t0) * 1000,
    )
    return summary


def _format_event_for_analysis(event_context: dict[str, Any]) -> str:
    """Build a concise text block describing a single event for LLM consumption."""
    lines: list[str] = []
    title = event_context.get("title") or "Untitled"
    lines.append(f"CURRENT UPCOMING EVENT: {title}")
    lines.append(
        f"Time: {event_context.get('local_start')} to {event_context.get('local_end') or 'TBD'}"
    )

    place = event_context.get("place") or {}
    if place:
        place_bits = [place.get("name"), place.get("city"), place.get("country")]
        location = ", ".join([b for b in place_bits if b])
        if location:
            lines.append(f"Location: {location}")

    lines.append(f"Type: {_format_list(event_context.get('types'))}")
    lines.append(f"Tags: {_format_list(event_context.get('tags'))}")

    contacts = event_context.get("contacts") or []
    lines.append(f"People: {_format_contacts(contacts)}")

    summary = event_context.get("summary") or ""
    if summary:
        lines.append("Current event notes:")
        lines.append(_condense_notes(summary))

    similar = event_context.get("similar_events") or []
    if similar:
        lines.append(f"Historical similar occurrences ({len(similar)}):")
        for s in similar:
            s_title = s.get("title") or "Untitled"
            s_date = s.get("local_start") or s.get("start_date") or ""
            s_summary = s.get("summary") or ""
            match_type = str(s.get("similarity_match_type") or "history").strip()
            overlap_ratio = s.get("attendee_overlap_ratio")
            if overlap_ratio is not None:
                lines.append(
                    f"  - {s_date} | {s_title} [match={match_type}, attendee_overlap={overlap_ratio}]"
                )
            else:
                lines.append(f"  - {s_date} | {s_title} [match={match_type}]")
            if s_summary:
                lines.append(f"    Notes: {_condense_notes(s_summary, limit=30)}")

    todos = event_context.get("todos") or []
    if todos:
        lines.append(f"Linked todos ({len(todos)}):")
        for todo in todos:
            lines.append(
                f"  - [{todo.get('status')}] {todo.get('description')}"
                f" (updated {todo.get('updated_at') or todo.get('created_at')})"
            )

    related_todos = event_context.get("related_todos") or []
    if related_todos:
        lines.append(f"Todos from past occurrences ({len(related_todos)}):")
        for todo in related_todos:
            source = todo.get("source_event") or "past event"
            lines.append(f"  - [{todo.get('status')}] {todo.get('description')} (from {source})")

    return "\n".join(lines)


def _research_event(
    event_text: str,
    title: str,
    timezone_name: str,
    *,
    event_context: dict[str, Any] | None = None,
    user_email: str | None = None,
) -> str:
    """Run a bounded web-research tool loop for a single event.

    Returns the LLM's research notes (may be empty if no research was needed
    or the call failed).
    """
    value_signals = _build_event_research_value_signals(
        title=title,
        event_text=event_text,
        event_context=event_context or {},
        user_email=user_email,
    )
    if not value_signals["should_research"]:
        logger.info(
            "[briefing.event] Research gate for '%s': skipped (score=%s, reasons=%s)",
            title,
            value_signals["score"],
            ", ".join(value_signals["reasons"]),
        )
        return ""

    plan = _plan_event_research(
        title=title,
        timezone_name=timezone_name,
        event_text=event_text,
        value_signals=value_signals,
        user_email=user_email,
    )
    if not plan.get("should_research"):
        logger.info(
            "[briefing.event] Research planner for '%s': skipped (%s)",
            title,
            plan.get("reason") or "no high-value target",
        )
        return ""

    research_profile = build_event_research_profile()
    if research_profile.build_tools_and_handlers is None:
        return ""
    tools, tool_handlers = research_profile.build_tools_and_handlers()
    if not tools:
        return ""
    runtime = research_profile.runtime
    system_prompt = (
        research_profile.get_system_prompt() if research_profile.get_system_prompt else ""
    )

    planned_targets = plan.get("targets") or []
    targets_block = "\n".join(
        f"- Query: {str(target.get('query') or '').strip()} | Why now: {str(target.get('why') or '').strip()}"
        for target in planned_targets
        if str(target.get("query") or "").strip()
    )
    research_prompt = (
        "You are preparing targeted event research for an upcoming calendar event.\n"
        "Research is allowed, but only if it provides high-value preparation context for this specific meeting.\n"
        "Do NOT return generic company descriptions, meeting hygiene, or trivia.\n"
        "If you cannot find high-signal context quickly, respond with exactly: NO_RESEARCH_NEEDED\n\n"
        "Use web_search and fetch_web_page only for the targeted research goals below.\n"
        "Maximum scope: up to 3 concise searches and focused page fetches.\n\n"
        "Output format (strict):\n"
        "- <finding>. Why it matters: <meeting-specific impact>. Source: <https://...>\n"
        "Only include findings that contain both a concrete 'Why it matters' and a valid source URL.\n\n"
        f"Timezone: {timezone_name}\n"
        f"Research rationale score: {value_signals['score']}\n"
        f"Rationale: {', '.join(value_signals['reasons'])}\n\n"
        f"Planned high-value targets:\n{targets_block or '- No valid targets identified'}\n\n"
        "Differentiate clearly between the current upcoming event and historical references.\n"
        f"{event_text}\n"
    )

    user_context = _get_daily_briefing_user_context(
        user_email,
        f"daily briefing event research {title}",
    )
    if user_context:
        research_prompt = f"{research_prompt}\n\nCURRENT USER CONTEXT:\n{user_context}"

    try:
        result = run_profiled_tool_loop(
            prompt=research_prompt,
            system_prompt=system_prompt,
            tools=tools,
            tool_handlers=tool_handlers,
            profile=runtime,
        )
        content = (result.get("content") or "").strip()
        tool_calls_made = result.get("tool_calls", 0)
        if content.upper().startswith("NO_RESEARCH_NEEDED"):
            logger.info(
                "[briefing.event] Research for '%s': NO_RESEARCH_NEEDED (%d tool call(s))",
                title,
                tool_calls_made,
            )
            return ""
        cleaned = _sanitize_research_findings(content)
        if not cleaned:
            logger.info(
                "[briefing.event] Research for '%s': dropped (no grounded findings) (%d tool call(s))",
                title,
                tool_calls_made,
            )
            return ""

        logger.info(
            "[briefing.event] Research for '%s': %d chars, %d tool call(s)",
            title,
            len(cleaned),
            tool_calls_made,
        )
        return cleaned
    except Exception:
        logger.warning("[briefing.event] Research failed for '%s', skipping", title, exc_info=True)
        return ""


def _build_event_research_value_signals(
    *,
    title: str,
    event_text: str,
    event_context: dict[str, Any],
    user_email: str | None,
) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []

    normalized_title = normalize_search_text(title)
    title_tokens = [token for token in re.split(r"[^a-z0-9]+", normalized_title) if token]
    if len(title_tokens) >= 2:
        score += 1
        reasons.append("specific_title")

    similar_events = event_context.get("similar_events") or []
    if not similar_events:
        score += 2
        reasons.append("no_history")
    else:
        historical_notes = [
            str(similar.get("summary") or "").strip() for similar in similar_events if similar.get("summary")
        ]
        if len(historical_notes) >= 2:
            score -= 1
            reasons.append("history_already_rich")

    contacts = event_context.get("contacts") or []
    external_contact_count = _count_external_contacts(contacts, user_email)
    if external_contact_count > 0:
        score += 2
        reasons.append("external_attendees")

    research_keywords = {
        "acquisition",
        "api",
        "architecture",
        "audit",
        "beta",
        "board",
        "compliance",
        "contract",
        "demo",
        "design",
        "kickoff",
        "launch",
        "legal",
        "migration",
        "negotiation",
        "okr",
        "partnership",
        "pilot",
        "planning",
        "pricing",
        "procurement",
        "proposal",
        "qbr",
        "quarterly",
        "renewal",
        "roadmap",
        "rfp",
        "risk",
        "sales",
        "security",
        "sla",
        "strategy",
        "technical",
        "vendor",
        "interview",
        "customer",
        "client",
        "onboarding",
        "integration",
        "incident",
        "postmortem",
        "retrospective",
        "discovery",
        "escalation",
        "funding",
        "investor",
        "press",
        "market",
        "research",
        "workshop",
        "summit",
        "conference",
        "webinar",
        "governance",
        "performance",
        "capacity",
        "supply",
        "logistics",
    }
    if any(token in research_keywords for token in title_tokens):
        score += 2
        reasons.append("high_signal_title")

    if "WEB RESEARCH FINDINGS" in event_text:
        score -= 2
        reasons.append("already_has_research")

    should_research = score >= 2
    return {
        "score": score,
        "reasons": reasons,
        "should_research": should_research,
        "external_contact_count": external_contact_count,
    }


def _count_external_contacts(contacts: list[dict[str, Any]], user_email: str | None) -> int:
    if not contacts:
        return 0
    owner_domain = ""
    if user_email and "@" in user_email:
        owner_domain = user_email.split("@", 1)[1].strip().lower()

    count = 0
    for contact in contacts:
        emails = [str(email).strip().lower() for email in (contact.get("emails") or []) if email]
        if not emails:
            comments = normalize_search_text(str(contact.get("comments") or ""))
            tags = [normalize_search_text(str(tag or "")) for tag in (contact.get("tags") or [])]
            if "external" in comments or any(tag == "external" for tag in tags):
                count += 1
            continue
        if owner_domain and any(email.endswith(f"@{owner_domain}") for email in emails):
            continue
        count += 1
    return count


def _plan_event_research(
    *,
    title: str,
    timezone_name: str,
    event_text: str,
    value_signals: dict[str, Any],
    user_email: str | None,
) -> dict[str, Any]:
    user_context = _get_daily_briefing_user_context(
        user_email,
        f"daily briefing research plan {title}",
    )
    prompt = (
        "Decide whether this event needs external web research and propose the smallest useful scope.\n"
        "Return strict JSON with keys: should_research (bool), reason (string), targets (array up to 3).\n"
        "Each target item must be: {\"query\": string, \"why\": string}.\n"
        "Only allow targets that could materially improve preparation for this exact event.\n"
        "Reject generic prep work, broad learning, and low-confidence fishing.\n"
        "If uncertain, set should_research=false.\n\n"
        f"Timezone: {timezone_name}\n"
        f"Value signals score: {value_signals.get('score')}\n"
        f"Signals: {', '.join(value_signals.get('reasons') or [])}\n\n"
        f"Event context:\n{event_text}\n"
    )
    if user_context:
        prompt = f"{prompt}\n\nUSER CONTEXT:\n{user_context}"

    try:
        planned_raw = call_llm_json(
            prompt,
            system_prompt=(
                "You are a strict research planner. Return JSON only."
            ),
            temperature=0,
            use_simpler_model=False,
        )
        planned = planned_raw if isinstance(planned_raw, dict) else {}
    except Exception:
        logger.warning("[briefing.event] Research planner failed for '%s'", title, exc_info=True)
        return {
            "should_research": bool(value_signals.get("should_research")),
            "reason": "planner_failed",
            "targets": [
                {
                    "query": title,
                    "why": "Find high-signal public context that can change prep decisions.",
                }
            ],
        }

    should_research = bool(planned.get("should_research"))
    reason = str(planned.get("reason") or "").strip() or "no_reason"
    raw_targets = planned.get("targets") if isinstance(planned.get("targets"), list) else []
    targets: list[dict[str, str]] = []
    for target in raw_targets[:3]:
        if not isinstance(target, dict):
            continue
        query = str(target.get("query") or "").strip()
        why = str(target.get("why") or "").strip()
        if not query or not why:
            continue
        targets.append({"query": query, "why": why})

    if should_research and not targets:
        return {
            "should_research": False,
            "reason": "planner_returned_no_targets",
            "targets": [],
        }
    return {
        "should_research": should_research,
        "reason": reason,
        "targets": targets,
    }


def _sanitize_research_findings(raw_text: str) -> str:
    lines: list[str] = []
    for raw_line in str(raw_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        normalized = line[1:].strip() if line.startswith("-") else line
        if not normalized:
            continue

        has_why = "why it matters:" in normalized.lower()
        has_source = bool(re.search(r"source:\s*https?://", normalized, re.IGNORECASE))
        if not (has_why and has_source):
            continue
        lines.append(f"- {normalized}")

    return "\n".join(lines)


def _synthesise_event_summary(
    event_text: str,
    research_notes: str,
    title: str,
    timezone_name: str,
    *,
    user_email: str | None = None,
) -> str:
    """Combine event data + research into a structured preparation summary."""
    research_block = ""
    if research_notes:
        research_block = (
            f"\n\nWEB RESEARCH FINDINGS (incorporate relevant points):\n{research_notes}"
        )

    user_context_block = ""
    if user_email:
        user_ctx = _get_daily_briefing_user_context(user_email, f"{title} {event_text[:200]}")
        if user_ctx:
            user_context_block = f"\n\n{user_ctx}\n"

    system_prompt = (
        "You are a concise briefing analyst. Analyze the event context below and produce a "
        "focused preparation summary. Output plain text with bullet points. No greetings, no "
        "meta-commentary. Be specific and actionable."
    )
    user_prompt = (
        f"Analyze this upcoming event and produce a preparation summary.\n"
        f"Timezone: {timezone_name}\n\n"
        f"{event_text}"
        f"{research_block}"
        f"{user_context_block}\n\n"
        "Use this interpretation rule:\n"
        "- 'CURRENT UPCOMING EVENT' and 'Current event notes' are about the event being prepared now.\n"
        "- 'Historical similar occurrences' are past references for pattern extraction only.\n"
        "- When historical similar occurrences exist, first summarize the most important points discussed in those past meetings before adding any new prep advice.\n"
        "Never mix up current commitments with historical notes.\n\n"
        "Perspective: this summary is for the calendar owner. Use second-person framing where useful\n"
        "(for example 'you will review metrics'). If owner names/aliases appear in notes,\n"
        "rewrite those references to second person. Avoid third-person self-references like\n"
        "'align with <owner name>' when referring to the owner.\n\n"
        "Quality bar (strict):\n"
        "- Only include non-obvious, high-value points grounded in the provided context/research.\n"
        "- Never invent facts. Every non-reading bullet MUST end with an evidence marker.\n"
        "- Evidence marker format: (evidence: history) or (evidence: todo) or (evidence: current_notes) or (evidence: research:https://url).\n"
        "- DO NOT include generic advice, like uploading documents somewhere if you don't know which documents you are talking about.\n"
        "- DO NOT suggest invite reminders, the meetings are already schedule and have their own reminders. Also do not suggest reaching out to people to remind them about the meeting or about its agenda.\n"
        "- DO NOT suggest testing/verifying audio or video quality, or testing connection speed.\n"
        "- All links, meeting times, credentials ARE correct and DO NOT require double-checking.\n"
        "- DO NOT output generic prep advice (for example, reviewing notes, checking agenda,\n"
        "  preparing talking points, or calendar hygiene).\n"
        "- Your goal is to do work that the user would have to do themselves, not to just point out what they need to do.\n"
        "- If there is no meaningful grounded insight, respond exactly with NO_MEANINGFUL_PREP.\n"
        "- If a section has no meaningful insight, omit that section.\n\n"
        "Respond with exactly these sections (skip a section if nothing relevant):\n"
        "KEY POINTS:\n"
        "- Start with 2-4 most important points discussed in past occurrences when available; otherwise use current notes (evidence required)\n\n"
        "ACTION ITEMS:\n"
        "- Pending todos, follow-ups, or preparation tasks (evidence required)\n\n"
        "SUGGESTED READING:\n"
        "- Links or material worth reviewing before this event (from research or notes)\n\n"
        "PREP FOCUS:\n"
        "- One sentence on what to prioritize before this event (evidence required)"
    )

    try:
        result = call_llm(
            user_prompt,
            system_prompt=system_prompt,
            temperature=0.1,
            use_simpler_model=False,
        )
        if str(result or "").strip().upper().startswith("NO_MEANINGFUL_PREP"):
            return ""
        return _sanitize_event_summary_output(result)
    except Exception:
        logger.warning("Failed to summarize event '%s', using fallback", title, exc_info=True)
        return ""


def _sanitize_event_summary_output(raw_text: str) -> str:
    """Filter low-value generic prep bullets from synthesized event summaries."""
    text = str(raw_text or "").strip()
    if not text:
        return ""

    section_order = ["KEY POINTS", "ACTION ITEMS", "SUGGESTED READING", "PREP FOCUS"]
    sections: dict[str, list[str]] = {name: [] for name in section_order}
    current_section = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        upper = line.rstrip(":").upper()
        if upper in sections:
            current_section = upper
            continue
        if not current_section:
            continue

        normalized = line[1:].strip() if line.startswith("-") else line
        if not normalized:
            continue
        if current_section != "SUGGESTED READING" and _is_low_value_prep_line(normalized):
            continue
        if current_section != "SUGGESTED READING":
            if not _EVIDENCE_MARKER_RE.search(normalized):
                continue
            normalized = _EVIDENCE_MARKER_RE.sub("", normalized).strip()
        elif not re.search(r"https?://", normalized, re.IGNORECASE):
            continue
        if not normalized:
            continue
        sections[current_section].append(normalized)

    rendered: list[str] = []
    for section in section_order:
        items = sections[section]
        if not items:
            continue
        rendered.append(f"{section}:")
        if section == "PREP FOCUS":
            rendered.append(f"- {items[0]}")
        else:
            for item in items:
                rendered.append(f"- {item}")
        rendered.append("")

    return "\n".join(rendered).strip()


def _is_low_value_prep_line(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return True
    for pattern in _LOW_VALUE_PREP_PATTERNS:
        if pattern.search(cleaned):
            return True
    return False


def _generate_markdown(
    context: dict[str, Any],
    *,
    selected_news: dict[str, Any] | None = None,
    user_email: str | None = None,
) -> str:
    header = f"# Daily Briefing - {context.get('date')} ({context.get('timezone')})"
    news_input = context.get("news_articles") or []
    selected_news_data: dict[str, Any]
    if selected_news is not None:
        selected_news_data = selected_news
    else:
        selected_news_data = context.get("selected_news") or {}
        if not selected_news_data:
            selected_news_data = (
                _select_news_for_generation(context)
                if news_input
                else {"topic_articles": [], "general_articles": []}
            )

    core_sections = ""
    news_section = ""

    if news_input:
        logger.info(
            "[briefing] News generation context: %d topic article(s), %d general headline(s)",
            len(selected_news_data["topic_articles"]),
            len(selected_news_data["general_articles"]),
        )
        t_parallel = perf_counter()
        with ThreadPoolExecutor(max_workers=BRIEFING_SECTION_MAX_WORKERS) as pool:
            core_future = pool.submit(
                _generate_event_sections_markdown, context, user_email=user_email
            )
            news_future = pool.submit(
                _generate_news_section_markdown,
                selected_news_data,
                user_email=user_email,
            )
            core_sections = core_future.result()
            news_section = news_future.result()
        logger.info(
            "[briefing] Parallel section generation complete (%.0fms)",
            (perf_counter() - t_parallel) * 1000,
        )
    else:
        core_sections = _generate_event_sections_markdown(context, user_email=user_email)

    event_validation = validate_event_sections(core_sections, context)
    if not event_validation.valid:
        logger.warning(
            "[briefing] Event section validation failed: %s; using deterministic fallback",
            event_validation.reasons,
        )
        core_sections = _build_event_sections_deterministic(context)

    birthdays_section = _render_birthdays_section(context.get("upcoming_birthdays") or [])
    todos_section = _render_outstanding_todos_section(context.get("all_todos") or [])

    sections = [header, core_sections, birthdays_section, todos_section]

    if news_input:
        news_validation = validate_news_section(news_section, has_news_input=True)
        if not news_validation.valid:
            logger.warning(
                "[briefing] News section validation failed: %s; using fallback",
                news_validation.reasons,
            )
            news_section = "## News & Topics\nNo notable news today."
        logger.info("[briefing] News section generated: %d chars", len(news_section))
        sections.append(news_section)

    return "\n\n".join(s.strip() for s in sections if s and s.strip())


def _generate_event_sections_markdown(
    context: dict[str, Any], *, user_email: str | None = None
) -> str:
    """Generate only event-critical sections in a focused call."""
    event_context = _format_event_generation_context(context)
    user_context = _get_daily_briefing_user_context(
        user_email,
        f"daily briefing events {context.get('date', '')}",
    )
    prompt = (
        "Generate ONLY these markdown sections, in this order:\n"
        "## Day Overview\n"
        "## Schedule\n"
        "## Event Prep\n\n"
        "Rules:\n"
        "- Focus only on today's events and linked prep/actions.\n"
        "- Day Overview must be strategic (1-2 bullets) and must NOT repeat schedule lines or times.\n"
        "- Schedule must stay factual (time, event title, optional location) with no extra advice.\n"
        "- Event Prep must contain non-obvious insights only; do not repeat schedule facts.\n"
        "- Use future-oriented prep language.\n"
        "- The reader is the calendar owner; use second-person framing where useful.\n"
        "- If the owner name/alias appears in context, rewrite it to 'you/your'.\n"
        "- Do not output birthdays, outstanding todos, or news sections.\n"
        "- Do not include any preamble or extra headers.\n\n"
        "If there are no events, output only:\n"
        "## Day Overview\n"
        "- No events scheduled today.\n\n"
        "Context:\n"
        f"{event_context}"
    )
    if user_context:
        prompt = f"{prompt}\n\nUSER CONTEXT:\n{user_context}"

    return call_llm(
        prompt,
        system_prompt=(
            "You write concise daily prep sections. Output markdown only with the requested sections."
        ),
        temperature=0.1,
        use_simpler_model=False,
    ).strip()


def _build_event_sections_deterministic(context: dict[str, Any]) -> str:
    events = context.get("events") or []
    if not events:
        return "## Day Overview\n- No events scheduled today."

    lines: list[str] = []
    lines.append("## Day Overview")
    lines.append(
        f"- You have {len(events)} upcoming event(s) today. Prioritize prep for the highest-impact meetings first."
    )

    lines.append("\n## Schedule")
    for event in events:
        time_label = _format_event_time_label(event.get("local_start"), event.get("local_end"))
        title = event.get("title") or "Untitled"
        location = _format_event_location(event.get("place") or {})
        if location:
            lines.append(f"- {time_label} - {title} ({location})")
        else:
            lines.append(f"- {time_label} - {title}")

    lines.append("\n## Event Prep")
    for event in events:
        time_label = _format_event_time_label(event.get("local_start"), event.get("local_end"))
        title = event.get("title") or "Untitled"
        lines.append(f"### {time_label} - {title}")
        deep_summary = event.get("deep_summary") or ""
        key_points, action_items, prep_focus = _extract_deep_summary_sections(deep_summary)
        if key_points:
            for point in key_points[:3]:
                lines.append(f"- {point}")
        if action_items:
            for action in action_items[:4]:
                lines.append(f"- {action}")
        linked_todos = event.get("todos") or []
        for todo in linked_todos[:3]:
            desc = str(todo.get("description") or "").strip()
            if desc:
                lines.append(f"- Pending todo: {desc}")
        if prep_focus:
            lines.append(f"- Prep focus: {prep_focus}")
        if not key_points and not action_items and not prep_focus and not linked_todos:
            lines.append("- No grounded prep insights available from current context.")
        lines.append("")
    return "\n".join(lines).strip()


def _format_event_generation_context(context: dict[str, Any]) -> str:
    events = context.get("events") or []
    lines: list[str] = [
        f"Date: {context.get('date')}",
        f"Timezone: {context.get('timezone')}",
        f"Events ({len(events)}):",
    ]
    for idx, event in enumerate(events, start=1):
        lines.append(f"{idx}. {event.get('title') or 'Untitled'}")
        lines.append(
            f"   Time: {_format_event_time_label(event.get('local_start'), event.get('local_end'))}"
        )
        location = _format_event_location(event.get("place") or {})
        if location:
            lines.append(f"   Location: {location}")
        deep_summary = (event.get("deep_summary") or "").strip()
        if deep_summary:
            lines.append("   Analysis:")
            for item in deep_summary.splitlines()[:12]:
                stripped = item.strip()
                if stripped:
                    lines.append(f"   {stripped}")
    return "\n".join(lines)


def _format_event_time_label(local_start: Any, local_end: Any) -> str:
    start_dt = _parse_datetime(local_start)
    end_dt = _parse_datetime(local_end)
    if not start_dt:
        return "Time TBD"
    start_label = start_dt.strftime("%H:%M")
    if end_dt:
        return f"{start_label}-{end_dt.strftime('%H:%M')}"
    return start_label


def _format_event_location(place: dict[str, Any]) -> str:
    bits = [place.get("name"), place.get("city"), place.get("country")]
    return ", ".join(str(bit).strip() for bit in bits if str(bit).strip())


def _extract_deep_summary_sections(deep_summary: str) -> tuple[list[str], list[str], str]:
    if not deep_summary.strip():
        return [], [], ""

    key_points: list[str] = []
    action_items: list[str] = []
    prep_focus = ""
    mode = ""
    for raw_line in deep_summary.splitlines():
        line = raw_line.strip()
        upper = line.upper().rstrip(":")
        if upper == "KEY POINTS":
            mode = "key"
            continue
        if upper == "ACTION ITEMS":
            mode = "action"
            continue
        if upper == "PREP FOCUS":
            mode = "focus"
            continue
        if not line:
            continue
        normalized = line[1:].strip() if line.startswith("-") else line
        if mode == "key":
            key_points.append(normalized)
        elif mode == "action":
            action_items.append(normalized)
        elif mode == "focus" and not prep_focus:
            prep_focus = normalized

    return key_points, action_items, prep_focus


def _render_birthdays_section(upcoming_birthdays: list[dict[str, Any]]) -> str:
    if not upcoming_birthdays:
        return ""
    lines = ["## Upcoming Birthdays"]
    for birthday in upcoming_birthdays:
        name = str(birthday.get("display_name") or "Unknown").strip()
        date_label = str(birthday.get("birthday") or "").strip()
        days_away = birthday.get("days_away")
        if birthday.get("is_today"):
            lines.append(f"- {name} - TODAY ({date_label})")
        else:
            lines.append(f"- {name} - {date_label} ({days_away} day(s) away)")
    return "\n".join(lines)


def _render_outstanding_todos_section(all_todos: list[dict[str, Any]]) -> str:
    lines = ["## Outstanding Todos"]
    if not all_todos:
        lines.append("- No pending todos for today.")
        return "\n".join(lines)
    for todo in all_todos:
        desc = str(todo.get("description") or "").strip()
        if not desc:
            continue
        lines.append(f"- {desc}")
    if len(lines) == 1:
        lines.append("- No pending todos for today.")
    return "\n".join(lines)


def _select_news_for_generation(context: dict[str, Any]) -> dict[str, Any]:
    """Select a bounded and relevance-ranked subset for news generation."""
    news_articles = context.get("news_articles") or []
    relevance_terms = _build_news_relevance_terms(context)
    user_email = str(context.get("user_email") or "").strip() or None

    topic_weights, source_weights = news_personalization.get_user_preference_weights(
        user_email=user_email
    )
    cluster_ids = [str(article.get("cluster_id") or "") for article in news_articles]
    cluster_signals = news_feeds.get_cluster_signal_map(cluster_ids, user_email=user_email)

    seen_keys: set[str] = set()
    scored_topic: list[tuple[float, dict[str, Any], str]] = []
    scored_general: list[tuple[float, dict[str, Any]]] = []

    for article in news_articles:
        cluster_id = str(article.get("cluster_id") or "")
        url = news_feeds.canonicalize_news_url(str(article.get("url") or "")).lower()
        title = (article.get("title") or "").strip().lower()
        key = url or cluster_id or title
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)

        score, breakdown = _score_news_article(
            article,
            relevance_terms,
            topic_weights=topic_weights,
            source_weights=source_weights,
            trend_signal=cluster_signals.get(cluster_id, {}),
        )
        enriched_article = dict(article)
        enriched_article["score_breakdown"] = breakdown
        matches = article.get("topic_matches") or []
        if matches:
            topic_label = str(matches[0]).strip() or "General"
            scored_topic.append((score, enriched_article, topic_label))
            continue

        scored_general.append((score, enriched_article))

    scored_topic.sort(key=lambda x: x[0], reverse=True)
    scored_general.sort(key=lambda x: x[0], reverse=True)

    desired_general_min = min(NEWS_GENERAL_MIN_SELECTED, len(scored_general))
    max_topic_slots = max(0, MAX_NEWS_SELECTED_ARTICLES - desired_general_min)

    topic_articles: list[dict[str, Any]] = []
    general_articles: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    topic_counts: dict[str, int] = {}
    used_clusters: set[str] = set()

    for _, article, topic in scored_topic:
        if len(topic_articles) + len(general_articles) >= MAX_NEWS_SELECTED_ARTICLES:
            break
        if len(topic_articles) >= max_topic_slots:
            break
        if topic_counts.get(topic, 0) >= NEWS_TOPIC_HARD_CAP:
            continue

        raw_score = float(article.get("score_breakdown", {}).get("total", 0.0) or 0.0)
        source_key = normalize_search_text(
            str(article.get("source_domain") or article.get("source") or "unknown")
        )
        repetition_penalty = source_counts.get(source_key, 0) * NEWS_SOURCE_REPEAT_PENALTY
        topic_penalty = topic_counts.get(topic, 0) * NEWS_TOPIC_REPEAT_PENALTY
        adjusted_score = raw_score - repetition_penalty - topic_penalty
        if adjusted_score < NEWS_TOPIC_MIN_SCORE:
            continue

        cluster_id = str(article.get("cluster_id") or "")
        if cluster_id and cluster_id in used_clusters:
            continue
        if cluster_id:
            used_clusters.add(cluster_id)

        topic_counts[topic] = topic_counts.get(topic, 0) + 1
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        article["score"] = round(adjusted_score, 4)
        article["section"] = "topic"
        article["topic_label"] = topic
        topic_articles.append(article)

    for _, article in scored_general:
        if len(topic_articles) + len(general_articles) >= MAX_NEWS_SELECTED_ARTICLES:
            break
        raw_score = float(article.get("score_breakdown", {}).get("total", 0.0) or 0.0)
        source_key = normalize_search_text(
            str(article.get("source_domain") or article.get("source") or "unknown")
        )
        repetition_penalty = source_counts.get(source_key, 0) * NEWS_SOURCE_REPEAT_PENALTY
        adjusted_score = raw_score - repetition_penalty
        if adjusted_score < NEWS_GENERAL_MIN_SCORE:
            continue

        cluster_id = str(article.get("cluster_id") or "")
        if cluster_id and cluster_id in used_clusters:
            continue
        if cluster_id:
            used_clusters.add(cluster_id)

        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        article["score"] = round(adjusted_score, 4)
        article["section"] = "general"
        general_articles.append(article)

    if len(general_articles) < desired_general_min:
        selected_general_keys = {
            news_feeds.canonicalize_news_url(str(article.get("url") or "")).lower()
            or str(article.get("cluster_id") or "")
            or normalize_search_text(str(article.get("title") or ""))
            for article in general_articles
        }
        for _, article in scored_general:
            if len(topic_articles) + len(general_articles) >= MAX_NEWS_SELECTED_ARTICLES:
                break
            if len(general_articles) >= desired_general_min:
                break

            article_key = (
                news_feeds.canonicalize_news_url(str(article.get("url") or "")).lower()
                or str(article.get("cluster_id") or "")
                or normalize_search_text(str(article.get("title") or ""))
            )
            if not article_key or article_key in selected_general_keys:
                continue

            cluster_id = str(article.get("cluster_id") or "")
            if cluster_id and cluster_id in used_clusters:
                continue
            if cluster_id:
                used_clusters.add(cluster_id)

            source_key = normalize_search_text(
                str(article.get("source_domain") or article.get("source") or "unknown")
            )
            raw_score = float(article.get("score_breakdown", {}).get("total", 0.0) or 0.0)
            repetition_penalty = source_counts.get(source_key, 0) * NEWS_SOURCE_REPEAT_PENALTY
            adjusted_score = raw_score - repetition_penalty

            source_counts[source_key] = source_counts.get(source_key, 0) + 1
            article["score"] = round(adjusted_score, 4)
            article["section"] = "general"
            general_articles.append(article)
            selected_general_keys.add(article_key)

    return {
        "topic_articles": topic_articles,
        "general_articles": general_articles,
    }


def _build_briefing_news_items(selected_news: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    rank = 1

    for article in selected_news.get("topic_articles") or []:
        items.append(
            {
                "briefing_item_id": f"briefing_news:{uuid4().hex}",
                "cluster_id": article.get("cluster_id"),
                "title": article.get("title"),
                "url": article.get("url"),
                "source": article.get("source"),
                "source_domain": article.get("source_domain"),
                "section": "topic",
                "topic_label": article.get("topic_label")
                or ((article.get("topic_matches") or [None])[0]),
                "rank": rank,
                "score": article.get("score"),
                "brief_summary": article.get("brief_summary"),
                "topic_matches": article.get("topic_matches") or [],
                "metadata": article.get("score_breakdown") or {},
            }
        )
        rank += 1

    for article in selected_news.get("general_articles") or []:
        items.append(
            {
                "briefing_item_id": f"briefing_news:{uuid4().hex}",
                "cluster_id": article.get("cluster_id"),
                "title": article.get("title"),
                "url": article.get("url"),
                "source": article.get("source"),
                "source_domain": article.get("source_domain"),
                "section": "general",
                "topic_label": None,
                "rank": rank,
                "score": article.get("score"),
                "brief_summary": article.get("brief_summary"),
                "topic_matches": article.get("topic_matches") or [],
                "metadata": article.get("score_breakdown") or {},
            }
        )
        rank += 1

    return items


def _build_news_relevance_terms(context: dict[str, Any]) -> set[str]:
    raw_terms: list[str] = []
    events = context.get("events") or []
    for event in events:
        raw_terms.append(event.get("title") or "")
        raw_terms.extend(event.get("tags") or [])
        for contact in event.get("contacts") or []:
            raw_terms.append(contact.get("display_name") or "")

    for todo in context.get("all_todos") or []:
        raw_terms.append(todo.get("description") or "")

    terms: set[str] = set()
    for value in raw_terms:
        normalized = normalize_search_text(value)
        if not normalized:
            continue
        for token in re.split(r"[^a-z0-9]+", normalized):
            if len(token) < 3 or token in _NEWS_STOPWORDS:
                continue
            terms.add(token)
    return terms


def _score_news_article(
    article: dict[str, Any],
    relevance_terms: set[str],
    *,
    topic_weights: dict[str, float] | None = None,
    source_weights: dict[str, float] | None = None,
    trend_signal: dict[str, float] | None = None,
) -> tuple[float, dict[str, float]]:
    topic_weights = topic_weights or {}
    source_weights = source_weights or {}
    trend_signal = trend_signal or {}

    score = 0.0
    breakdown: dict[str, float] = {}
    source = normalize_search_text(str(article.get("source") or ""))
    for source_key, weight in _NEWS_SOURCE_QUALITY.items():
        if source_key in source:
            score += weight
            breakdown["source_quality"] = float(weight)
            break

    topic_matches = article.get("topic_matches") or []
    topic_match_score = min(len(topic_matches), 3) * 2.0
    score += topic_match_score
    breakdown["topic_match"] = float(topic_match_score)

    title = normalize_search_text(str(article.get("title") or ""))
    summary = normalize_search_text(str(article.get("summary") or ""))
    haystack = f"{title} {summary}"
    overlap = sum(1 for term in relevance_terms if term and term in haystack)
    overlap_score = overlap * 1.5
    score += overlap_score
    breakdown["context_overlap"] = float(overlap_score)

    trend_score = float(trend_signal.get("trend_score") or 0.0)
    novelty_penalty = float(trend_signal.get("novelty_penalty") or 0.0)
    trend_boost = min(trend_score, 4.0) * 0.8
    novelty_delta = min(novelty_penalty, 3.0) * 0.9
    score += trend_boost
    score -= novelty_delta
    breakdown["trend_boost"] = float(trend_boost)
    breakdown["novelty_penalty"] = float(-novelty_delta)

    preference_score = 0.0
    for label in topic_matches:
        pref = topic_weights.get(normalize_search_text(str(label)), 0.0)
        preference_score += pref
    source_pref_key = normalize_search_text(
        str(article.get("source_domain") or article.get("source") or "")
    )
    preference_score += source_weights.get(source_pref_key, 0.0)
    score += preference_score
    breakdown["user_preference"] = float(preference_score)

    published_at = article.get("published_at")
    published_dt = _parse_datetime(published_at)
    if published_dt:
        if published_dt.tzinfo is None:
            published_dt = published_dt.replace(tzinfo=timezone.utc)
        age_hours = max((datetime.now(timezone.utc) - published_dt).total_seconds() / 3600, 0)
        if age_hours <= 12:
            score += 2.0
            breakdown["recency"] = 2.0
        elif age_hours <= 24:
            score += 1.0
            breakdown["recency"] = 1.0

    breakdown["total"] = float(score)

    return score, breakdown


def _generate_news_section_markdown(
    selected_news: dict[str, Any],
    *,
    user_email: str | None = None,
) -> str:
    topic_articles = selected_news.get("topic_articles") or []
    general_articles = selected_news.get("general_articles") or []
    sections: list[str] = ["## News & Topics"]
    wrote_any = False
    seen_urls: set[str] = set()

    for label, articles in _group_topic_articles(topic_articles):
        lines = _render_news_article_lines(articles, seen_urls=seen_urls)
        if not lines:
            continue
        wrote_any = True
        sections.append(f"### {label}")
        sections.extend(lines)

    general_lines = _render_news_article_lines(general_articles, seen_urls=seen_urls)
    if general_lines:
        wrote_any = True
        sections.append("### General Headlines")
        sections.extend(general_lines)

    if not wrote_any:
        sections.append("No notable news today.")

    return "\n".join(sections)


def _enrich_selected_news_summaries(
    selected_news: dict[str, Any],
    *,
    user_email: str | None = None,
) -> dict[str, Any]:
    """Add LLM-authored one-sentence summaries for selected news articles."""
    topic_articles = [dict(article) for article in (selected_news.get("topic_articles") or [])]
    general_articles = [dict(article) for article in (selected_news.get("general_articles") or [])]
    total_articles = len(topic_articles) + len(general_articles)
    if total_articles == 0:
        return {
            "topic_articles": topic_articles,
            "general_articles": general_articles,
        }

    logger.info("[briefing] Enriching %d selected article summary(s)", total_articles)

    article_slots: list[tuple[list[dict[str, Any]], int, dict[str, Any]]] = []
    for idx, article in enumerate(topic_articles):
        article_slots.append((topic_articles, idx, article))
    for idx, article in enumerate(general_articles):
        article_slots.append((general_articles, idx, article))

    workers = min(NEWS_SUMMARY_MAX_WORKERS, total_articles)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(_generate_article_brief_summary, article, user_email=user_email): (
                container,
                idx,
            )
            for container, idx, article in article_slots
        }
        for future in as_completed(future_map):
            container, idx = future_map[future]
            article = container[idx]
            fallback = _to_single_news_sentence(str(article.get("summary") or "").strip())
            if not fallback:
                fallback = "Notable development worth tracking for your priorities."
            try:
                brief_summary = (future.result() or "").strip()
            except Exception:
                logger.warning("[briefing] Failed to generate article brief summary", exc_info=True)
                brief_summary = ""
            article["brief_summary"] = brief_summary or fallback

    return {
        "topic_articles": topic_articles,
        "general_articles": general_articles,
    }


def _generate_article_brief_summary(
    article: dict[str, Any],
    *,
    user_email: str | None = None,
) -> str:
    """Generate one concise sentence helping the user decide whether to read."""
    title = str(article.get("title") or "Untitled").strip()
    source = str(article.get("source") or "Unknown").strip()
    raw_summary = str(article.get("summary") or "").strip()
    url = str(article.get("url") or "").strip()
    topic_labels = ", ".join(str(t).strip() for t in (article.get("topic_matches") or []) if t)

    system_prompt = (
        "You write one-sentence news briefs for a daily briefing. "
        "The sentence must be concrete and useful for deciding whether to open the full article. "
        "Use plain text only (no markdown, no links). Use ASCII characters only. "
        "Avoid vague language and avoid repeating the title verbatim."
    )
    user_context = _get_daily_briefing_user_context(
        user_email,
        f"daily briefing article summary {title}",
    )
    if user_context:
        system_prompt = f"{system_prompt}\n\n{user_context}"

    prompt = (
        "Write exactly one sentence (max 300 chars) that states the key development and why it matters.\n"
        f"Title: {title}\n"
        f"Source: {source}\n"
        f"URL: {url or 'N/A'}\n"
        f"Topics: {topic_labels or 'General'}\n"
        f"Raw content: {raw_summary or 'N/A'}"
    )

    try:
        generated = call_llm(
            prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            use_simpler_model=False,
        )
    except Exception:
        logger.warning("[briefing] LLM article summary generation failed for '%s'", title, exc_info=True)
        return ""

    candidate = _to_single_news_sentence(str(generated or "").strip())
    if not candidate:
        return ""
    if len(candidate) > 240:
        candidate = candidate[:237].rstrip() + "..."
    return candidate


def _group_topic_articles(topic_articles: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    ordered_labels: list[str] = []
    for article in topic_articles:
        label = str((article.get("topic_matches") or ["General"])[0]).strip() or "General"
        if label not in grouped:
            grouped[label] = []
            ordered_labels.append(label)
        grouped[label].append(article)
    return [(label, grouped[label]) for label in ordered_labels]


def _render_news_article_lines(
    articles: list[dict[str, Any]],
    *,
    seen_urls: set[str] | None = None,
) -> list[str]:
    lines: list[str] = []
    dedupe_urls = seen_urls if seen_urls is not None else set()
    for article in articles:
        title = str(article.get("title") or "Untitled").strip()
        url = str(article.get("url") or "").strip()
        canonical_url = news_feeds.canonicalize_news_url(url).lower()
        source = str(article.get("source") or "Unknown").strip()
        summary = _to_single_news_sentence(str(article.get("brief_summary") or "").strip())
        if not summary:
            summary = _to_single_news_sentence(str(article.get("summary") or "").strip())

        if not url:
            continue
        if canonical_url and canonical_url in dedupe_urls:
            continue
        if canonical_url:
            dedupe_urls.add(canonical_url)
        if not summary:
            summary = "Notable development worth tracking for your priorities."
        lines.append(f"- [{title}]({url}) - {summary} ({source})")
    return lines


def _to_single_news_sentence(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if not cleaned:
        return ""
    first = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0].strip()
    if len(first) > 300:
        first = first[:297].rstrip() + "..."
    if first and first[-1] not in ".!?":
        first += "."
    return first


def _build_briefing_prompt(context: dict[str, Any]) -> str:
    has_birthdays = bool(context.get("upcoming_birthdays"))
    birthdays_section = (
        (
            "## Upcoming Birthdays\n"
            "- List each person with their birthday date and how many days away.\n"
            "- If a birthday is today, highlight it.\n"
        )
        if has_birthdays
        else ""
    )
    birthdays_note = (
        ("\nIf there are upcoming birthdays, include the Upcoming Birthdays section.\n")
        if has_birthdays
        else ""
    )
    has_news = bool(context.get("news_articles"))
    news_section = (
        (
            "## News & Topics\n"
            "- Use ONLY concrete articles from the context below. Do NOT invent or generalize.\n"
            "- First list topic-matched articles (grouped by topic label), then up to 5 notable\n"
            "  general headlines that pass relevance and worldwide importance thresholds.\n"
            "- Each item MUST follow this exact format:\n"
            "  [Article Title](url) - one sentence explaining why it matters or what happened. (Source)\n"
            "- The 1-sentence summary is the most valuable part -- the user should understand the\n"
            "  story at a glance without clicking.\n"
            "- NEVER produce generic category lists like 'AI and machine learning' or\n"
            "  'Politics (e.g., ...)'. Every bullet must reference a specific article with its URL.\n"
            "- NEVER say 'there are several articles' or describe the data -- just write the items.\n"
            "- If no articles have useful content, write: 'No notable news today.'\n"
        )
        if has_news
        else ""
    )
    news_note = (
        (
            "\nIf there are news articles, include the News & Topics section after "
            "Outstanding Todos.\n"
        )
        if has_news
        else ""
    )

    return (
        "DAILY BRIEFING TASK (PREP DOCUMENT)\n"
        "You are preparing the user for upcoming events. Use future tense (will, upcoming, prepare, review).\n"
        "\n"
        "OUTPUT RULES (MANDATORY):\n"
        "- Output ONLY the final briefing Markdown. No preamble, no sign-off, no reasoning.\n"
        "- NEVER include internal thinking, chain-of-thought, or step-by-step planning.\n"
        "- NEVER prefix with explanations of what you will do ('Let me analyze', 'First I will').\n"
        "- NEVER ask questions or offer to do more.\n"
        "- NEVER use meta-commentary about the input ('the text includes', 'there are several',\n"
        "  'you provided', 'the text appears', 'it appears', 'none mentioned explicitly',\n"
        "  'based on the data provided', 'based on what was provided').\n"
        "- NEVER produce generic category lists — every bullet must contain a specific fact,\n"
        "  title, action item, or recommendation.\n"
        "- Focus ONLY on today's events and their linked todos.\n"
        "- Each event already has a pre-computed analysis with key points, action items, and prep\n"
        "  focus. Incorporate that analysis into the Event Prep section -- do NOT ignore it.\n"
        "\n"
        "TOOL GUIDANCE:\n"
        "- Use search_memories for event-specific past notes.\n"
        "- Use get_document only when a document should be reviewed for a specific event.\n"
        "- Use web_search for external prep (company background, agenda context, travel).\n"
        "- Use fetch_web_page only for URLs discovered via web_search.\n"
        "\n"
        "REQUIRED STRUCTURE:\n"
        "# Daily Briefing - <date> (<timezone>)\n"
        "## Day Overview\n"
        "- Summarize the day, key prep actions, and conflicts.\n"
        "## Schedule\n"
        "- One bullet per event: <local time> - <title> (location if available)\n"
        "## Event Prep\n"
        "### <local time> - <title>\n"
        "- Key points (from the per-event analysis)\n"
        "- Action items and pending todos\n"
        "- Suggested prep focus\n"
        f"{birthdays_section}"
        "## Outstanding Todos\n"
        "- List pending todos only.\n"
        f"{news_section}"
        "\n"
        "If there are no events, say so in Day Overview and skip Schedule/Event Prep sections.\n"
        "If there are no relevant todos, still include Outstanding Todos with a short note.\n"
        f"{birthdays_note}"
        f"{news_note}"
        "\n"
        "CONTEXT FOR TODAY (already filtered; every event below is UPCOMING today):\n"
        f"{_format_context_text(context)}"
    )


def _generate_summary(
    context: dict[str, Any],
    markdown: str,
    *,
    user_email: str | None = None,
) -> str:
    del markdown  # Summary is deterministic and does not inspect full markdown content.
    del user_email  # Summary should not vary based on inferred user preference context.

    event_count = len(context.get("events") or [])
    todo_count = _count_todos(context.get("events") or [], context.get("all_todos") or [])
    weather_summary = str(context.get("weather_summary") or "").strip()

    meeting_label = "meeting" if event_count == 1 else "meetings"
    todo_label = "todo" if todo_count == 1 else "todos"
    parts = [f"Today: {event_count} {meeting_label} and {todo_count} pending {todo_label}."]
    if weather_summary:
        parts.append(weather_summary)

    summary = " ".join(part.strip() for part in parts if part and part.strip())
    validation = validate_summary(summary)
    if validation.valid:
        return summary

    logger.warning("[briefing] Deterministic summary failed validation: %s", validation.reasons)
    cleaned = re.sub(r"\s+", " ", summary).replace("#", "").strip()
    if len(cleaned) > 500:
        cleaned = cleaned[:497].rstrip() + "..."
    return cleaned


def _build_weather_summary(
    *,
    local_date: date,
    timezone_name: str,
    user_email: str | None,
) -> str:
    if not user_email:
        return ""

    try:
        from user_locations import get_last_known_location
        from weather_forecast import build_daily_weather_summary

        location = get_last_known_location(user_email)
        if not location:
            return ""
        return build_daily_weather_summary(
            location=location,
            target_date=local_date,
            timezone_name=timezone_name,
        )
    except Exception:
        logger.warning("[briefing] Weather summary unavailable", exc_info=True)
        return ""


def _condense_notes(notes: str, limit: int = 48) -> str:
    cleaned = notes.replace("\r\n", "\n")
    lines = [line.strip() for line in cleaned.split("\n") if line.strip()]
    condensed: list[str] = []
    for line in lines:
        if line.startswith("#"):
            continue
        if line.startswith("-"):
            condensed.append(line)
        else:
            condensed.append(f"- {line}")
        if len(condensed) >= limit:
            break
    return "\n".join(condensed)


def _format_context_text(context: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Context Overview (for upcoming events):")
    lines.append(f"Date: {context.get('date')}")
    lines.append(f"Timezone: {context.get('timezone')}")
    lines.append(f"Day Start: {context.get('day_start')}")
    lines.append(f"Day End: {context.get('day_end')}")
    lines.append("")

    events = context.get("events") or []
    lines.append(f"Events for Today ({len(events)}):")
    if not events:
        lines.append("- None")
    for idx, event in enumerate(events, start=1):
        title = event.get("title") or "Untitled"
        lines.append(f"Event {idx} of today: {title}")
        lines.append(f"- Time: {event.get('local_start')} to {event.get('local_end') or 'TBD'}")
        lines.append(f"- Type: {_format_list(event.get('types'))}")
        lines.append(f"- Tags: {_format_list(event.get('tags'))}")
        place = event.get("place") or {}
        if place:
            place_bits = [place.get("name"), place.get("city"), place.get("country")]
            location = ", ".join([b for b in place_bits if b])
            if location:
                lines.append(f"- Location: {location}")

        contacts = event.get("contacts") or []
        lines.append(f"- People: {_format_contacts(contacts)}")

        # Use the pre-computed deep summary instead of raw similar events / todos
        deep_summary = event.get("deep_summary") or ""
        if deep_summary:
            lines.append("- Analysis (key points, action items, prep focus):")
            for dl in deep_summary.split("\n"):
                stripped = dl.strip()
                if stripped:
                    lines.append(f"  {stripped}")
        else:
            # Fallback: include minimal raw data if per-event LLM failed
            summary = event.get("summary") or ""
            if summary:
                lines.append("- Context from prior notes (for prep):")
                lines.append(_condense_notes(summary))
            todos = event.get("todos") or []
            if todos:
                lines.append(f"- Event Todos ({len(todos)}):")
                for todo in todos:
                    lines.append(f"  - [{todo.get('status')}] {todo.get('description')}")
        lines.append("")

    # -- Upcoming birthdays section --
    birthdays = context.get("upcoming_birthdays") or []
    if birthdays:
        lines.append(f"Upcoming Birthdays ({len(birthdays)}):")
        for b in birthdays:
            name = b.get("display_name") or "Unknown"
            if b.get("is_today"):
                lines.append(f"- {name} - TODAY!")
            else:
                days = b.get("days_away", "?")
                bday = b.get("birthday") or ""
                lines.append(f"- {name} - in {days} day(s) ({bday})")
        lines.append("")

    # -- News & Topics section --
    news_articles = context.get("news_articles") or []
    if news_articles:
        topic_matched = [a for a in news_articles if a.get("topic_matches")]
        unmatched = [a for a in news_articles if not a.get("topic_matches")]
        if topic_matched:
            lines.append(f"News Matching Your Topics ({len(topic_matched)}):")
            for a in topic_matched:
                topics_str = ", ".join(a["topic_matches"])
                url = (a.get("url") or "").strip()
                title = a.get("title", "")
                lines.append(f"- [{topics_str}] {title} ({a.get('source', '')})")
                if url:
                    lines.append(f"  URL: {url}")
                summary = (a.get("summary") or "").strip()
                if summary:
                    lines.append(f"  {summary[:200]}")
            lines.append("")
        if unmatched:
            lines.append(f"General Headlines ({len(unmatched)}):")
            for a in unmatched[:5]:  # cap to top 5 important worldwide headlines
                url = (a.get("url") or "").strip()
                title = a.get("title", "")
                lines.append(f"- {title} ({a.get('source', '')})")
                if url:
                    lines.append(f"  URL: {url}")
                summary = (a.get("summary") or "").strip()
                if summary:
                    lines.append(f"  {summary[:200]}")
            lines.append("")

    all_todos = context.get("all_todos") or []
    lines.append(f"Unlinked Relevant Todos ({len(all_todos)}):")
    if not all_todos:
        lines.append("- None")
    for todo in all_todos:
        lines.append(
            "- "
            f"[{todo.get('status')}] {todo.get('description')}"
            f" (updated {todo.get('updated_at') or todo.get('created_at')})"
        )
    return "\n".join(lines)


def _format_list(values: Any) -> str:
    if not values:
        return "None"
    if isinstance(values, (list, tuple, set)):
        cleaned = [str(v) for v in values if v]
        return ", ".join(cleaned) if cleaned else "None"
    return str(values)


def _format_contacts(contacts: list[dict[str, Any]]) -> str:
    if not contacts:
        return "None"
    names = [
        str(contact.get("display_name")) for contact in contacts if contact.get("display_name")
    ]
    return ", ".join(names) if names else "None"


def _collect_related_event_todos(similar_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not similar_events:
        return []
    related: list[dict[str, Any]] = []
    seen: set[str] = set()
    for similar in similar_events:
        event_id = similar.get("id")
        if not event_id:
            continue
        todos = todos_service.list_event_todos(event_id, pending_only=True)
        source_label = similar.get("title") or event_id
        for todo in todos:
            todo_id = todo.get("todo_id") or ""
            if todo_id and todo_id in seen:
                continue
            if todo_id:
                seen.add(todo_id)
            related.append({**todo, "source_event": source_label})
    return related


def _count_todos(
    event_contexts: list[dict[str, Any]],
    unlinked_todos: list[dict[str, Any]],
) -> int:
    count = len(unlinked_todos)
    for event in event_contexts:
        count += len(event.get("todos") or [])
        count += len(event.get("related_todos") or [])
    return count


def _parse_datetime(value: Any | None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _format_local(value: datetime | None, tz: ZoneInfo) -> str | None:
    if not value:
        return None
    localized = value.astimezone(tz) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return localized.astimezone(tz).isoformat()


def _isoformat(value: Any | None) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return None
