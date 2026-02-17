from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

import daily_briefings
import news_feeds
import retrieval
import todos as todos_service
from agent.tool_loop_runner import run_profiled_tool_loop
from agents.daily_briefing.profile import (
    build_daily_briefing_agent_profile,
    build_event_research_profile,
)
from db import get_conn
from llm_helpers import call_llm

logger = logging.getLogger(__name__)

DEFAULT_SIMILAR_LIMIT = 4
BIRTHDAY_LOOKAHEAD_DAYS = 7


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
    events = [_apply_timezone(event, tz) for event in _fetch_events_for_span(start_utc, end_utc)]
    event_contexts: list[dict[str, Any]] = []
    for event in events:
        similar_events = [
            _apply_timezone(similar, tz)
            for similar in _fetch_similar_events(event, start_utc, DEFAULT_SIMILAR_LIMIT)
        ]
        event_todos = todos_service.list_event_todos(event["id"])
        related_todos = _collect_related_event_todos(similar_events)
        contacts = _fetch_contact_summaries(event.get("people") or [])
        event_contexts.append(
            {
                **event,
                "similar_events": similar_events,
                "todos": event_todos,
                "related_todos": related_todos,
                "contacts": contacts,
            }
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

    # -- 2. Per-event deep analysis (dedicated LLM call each) --------------------
    t0 = perf_counter()
    for ec in event_contexts:
        ec["deep_summary"] = _summarize_event(ec, timezone_name)
    logger.info(
        "[briefing] Deep analysis: %d event(s) analyzed (%.0fms)",
        len(event_contexts),
        (perf_counter() - t0) * 1000,
    )

    # -- 3. Upcoming birthdays ---------------------------------------------------
    upcoming_birthdays = _fetch_upcoming_birthdays(local_date)
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

    # -- 4. News aggregation -----------------------------------------------------
    t0 = perf_counter()
    try:
        news_articles = news_feeds.fetch_news()
    except Exception:
        logger.warning("News feed aggregation failed, continuing without news", exc_info=True)
        news_articles = []
    topic_matched_count = sum(1 for a in news_articles if a.get("topic_matches"))
    logger.info(
        "[briefing] News: %d article(s) (%d topic-matched, %d general) (%.0fms)",
        len(news_articles),
        topic_matched_count,
        len(news_articles) - topic_matched_count,
        (perf_counter() - t0) * 1000,
    )

    # -- 5. Unlinked todos -------------------------------------------------------
    all_todos = todos_service.list_unlinked_relevant_todos()
    logger.info("[briefing] Unlinked todos: %d", len(all_todos))

    context = {
        "date": local_date.isoformat(),
        "timezone": timezone_name,
        "day_start": start_local.isoformat(),
        "day_end": end_local.isoformat(),
        "events": event_contexts,
        "all_todos": all_todos,
        "upcoming_birthdays": upcoming_birthdays,
        "news_articles": news_articles,
    }

    # -- 6. Assemble final markdown & summary ------------------------------------
    t0 = perf_counter()
    markdown = _generate_markdown(context)
    logger.info(
        "[briefing] Markdown generated: %d chars (%.0fms)",
        len(markdown),
        (perf_counter() - t0) * 1000,
    )

    t0 = perf_counter()
    summary = _generate_summary(context, markdown)
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


def _fetch_similar_events(
    event: dict[str, Any],
    day_start: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    title = (event.get("title") or "").strip()
    if not title or limit <= 0:
        return []
    matches = _fetch_similar_by_title(event["id"], title, day_start, limit)
    if len(matches) >= limit:
        return matches

    remaining = limit - len(matches)
    recurrence_key = _extract_recurrence_key(event)
    if not recurrence_key:
        return matches

    recurrence_matches = _fetch_similar_by_recurrence(
        event["id"],
        recurrence_key,
        day_start,
        remaining,
    )
    return matches + recurrence_matches


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


def _summarize_event(event_context: dict[str, Any], timezone_name: str) -> str:
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
    research_notes = _research_event(event_text, title, timezone_name)
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
    summary = _synthesise_event_summary(event_text, research_notes, title, timezone_name)
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
    lines.append(f"Event: {title}")
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
        lines.append("Notes from this event:")
        lines.append(_condense_notes(summary))

    similar = event_context.get("similar_events") or []
    if similar:
        lines.append(f"Past occurrences ({len(similar)}):")
        for s in similar:
            s_title = s.get("title") or "Untitled"
            s_date = s.get("local_start") or s.get("start_date") or ""
            s_summary = s.get("summary") or ""
            lines.append(f"  - {s_date} | {s_title}")
            if s_summary:
                lines.append(f"    Notes: {_condense_notes(s_summary, limit=6)}")

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


def _research_event(event_text: str, title: str, timezone_name: str) -> str:
    """Run a bounded web-research tool loop for a single event.

    Returns the LLM's research notes (may be empty if no research was needed
    or the call failed).
    """
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

    research_prompt = (
        "You are preparing background research for an upcoming calendar event.\n"
        "If external context would help the user prepare (company info, public agenda, "
        "venue details, relevant news, suggested reading material), use the web_search "
        "and fetch_web_page tools to gather it. Keep searches targeted and concise.\n"
        "If the event is routine or internal with no obvious research angle, "
        "respond with: NO_RESEARCH_NEEDED\n\n"
        f"Timezone: {timezone_name}\n\n"
        f"{event_text}\n\n"
        "Return your research findings as concise bullet points. "
        "Include source URLs when available."
    )

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
        logger.info(
            "[briefing.event] Research for '%s': %d chars, %d tool call(s)",
            title,
            len(content),
            tool_calls_made,
        )
        return content
    except Exception:
        logger.warning("[briefing.event] Research failed for '%s', skipping", title, exc_info=True)
        return ""


def _synthesise_event_summary(
    event_text: str, research_notes: str, title: str, timezone_name: str
) -> str:
    """Combine event data + research into a structured preparation summary."""
    research_block = ""
    if research_notes:
        research_block = (
            f"\n\nWEB RESEARCH FINDINGS (incorporate relevant points):\n{research_notes}"
        )

    system_prompt = (
        "You are a concise briefing analyst. Analyze the event context below and produce a "
        "focused preparation summary. Output plain text with bullet points. No greetings, no "
        "meta-commentary. Be specific and actionable."
    )
    user_prompt = (
        f"Analyze this upcoming event and produce a preparation summary.\n"
        f"Timezone: {timezone_name}\n\n"
        f"{event_text}"
        f"{research_block}\n\n"
        "Respond with exactly these sections (skip a section if nothing relevant):\n"
        "KEY POINTS:\n"
        "- Important context from past occurrences or notes\n\n"
        "ACTION ITEMS:\n"
        "- Pending todos, follow-ups, or preparation tasks\n\n"
        "SUGGESTED READING:\n"
        "- Links or material worth reviewing before this event (from research or notes)\n\n"
        "PREP FOCUS:\n"
        "- One sentence on what to prioritize before this event"
    )

    try:
        result = call_llm(
            user_prompt,
            system_prompt=system_prompt,
            temperature=0.1,
            use_simpler_model=False,
        )
        return result.strip()
    except Exception:
        logger.warning("Failed to summarize event '%s', using fallback", title, exc_info=True)
        return ""


def _generate_markdown(context: dict[str, Any]) -> str:
    agent_profile = build_daily_briefing_agent_profile()
    if agent_profile.build_tools_and_handlers is None:
        raise RuntimeError("daily_briefing profile missing tool policy")
    if agent_profile.get_system_prompt is None:
        raise RuntimeError("daily_briefing profile missing system prompt")
    tools, tool_handlers = agent_profile.build_tools_and_handlers()
    runtime_profile = agent_profile.runtime
    system_prompt = agent_profile.get_system_prompt()
    prompt = _build_briefing_prompt(context)
    result = run_profiled_tool_loop(
        prompt=prompt,
        system_prompt=system_prompt,
        tools=tools,
        tool_handlers=tool_handlers,
        profile=runtime_profile,
    )
    content = result.get("content", "")
    if _is_invalid_briefing(content):
        logger.warning(
            "[briefing] Markdown failed validation, triggering rewrite (draft %d chars)",
            len(content),
        )
        t_rewrite = perf_counter()
        retry_prompt = _build_rewrite_prompt(context, content)
        content = call_llm(
            retry_prompt,
            system_prompt="Rewrite strictly to the required format.",
            temperature=0.1,
            use_simpler_model=False,
        )
        logger.info(
            "[briefing] Rewrite complete: %d chars, valid=%s (%.0fms)",
            len(content),
            not _is_invalid_briefing(content),
            (perf_counter() - t_rewrite) * 1000,
        )
    return content


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
            "  general headlines that cover important worldwide events.\n"
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
        "- Output Markdown only. No preamble, no sign-off.\n"
        "- NEVER ask questions or offer to do more.\n"
        "- NEVER use meta-commentary about the input ('the text includes', 'there are several',\n"
        "  'you provided', 'the text appears', 'it appears', 'none mentioned explicitly').\n"
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
        "- List pending todos and recently completed ones (last 2 weeks).\n"
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


def _build_rewrite_prompt(context: dict[str, Any], draft: str) -> str:
    return (
        "Rewrite the draft into the REQUIRED STRUCTURE. Output Markdown only.\n"
        "RULES:\n"
        "- NEVER use meta-commentary ('the text includes', 'there are several', 'it appears').\n"
        "- NEVER produce generic category lists — every bullet must reference a specific item.\n"
        "- NEVER ask questions or offer to do more.\n"
        "- For News & Topics: each item must be a specific article with URL and 1-sentence summary.\n"
        "  Format: [Title](url) - why it matters. (Source)\n"
        "- If no useful news, write: 'No notable news today.'\n"
        "\n"
        "Draft (do not include any of this meta text in output):\n"
        f"{draft}\n\n"
        "Context (use only for content):\n"
        f"{_format_context_text(context)}"
    )


def _is_invalid_briefing(content: str) -> bool:
    if not content.strip().startswith("# Daily Briefing"):
        return True
    lower = content.lower()
    banned = [
        "it appears",
        "you provided",
        "the text",
        "to help you",
        "let me know",
        "clarify",
        "if you have",
        "if you'd like",
        "if you would like",
        "there are several",
        "none mentioned explicitly",
        "please let me know",
        "the text includes",
        "various topics",
        "including ai,",
        "e.g.,",
        "and more.",
        "extract specific information",
    ]
    return any(phrase in lower for phrase in banned)


def _generate_summary(context: dict[str, Any], markdown: str) -> str:
    system_prompt = (
        "You summarize daily briefings into a single short paragraph. This is future-oriented prep. "
        "Output plain text only. Keep it under 2 sentences. Use ASCII characters only. Mention "
        "meeting count and todo count when available. Be practical and direct."
    )
    prompt = (
        "Generate a daily summary for the user based on this information.\n"
        f"Event count: {len(context.get('events') or [])}\n"
        f"Todo count: {_count_todos(context.get('events') or [], context.get('all_todos') or [])}\n"
        "Briefing markdown (reference only):\n"
        f"{markdown}"
    )
    return call_llm(
        prompt,
        system_prompt=system_prompt,
        temperature=0.2,
        use_simpler_model=False,
    )


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
        todos = todos_service.list_event_todos(event_id)
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
