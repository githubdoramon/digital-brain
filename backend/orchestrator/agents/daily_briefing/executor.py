from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import daily_briefings
import retrieval
import todos as todos_service
from agent.tool_loop_runner import run_profiled_tool_loop
from agents.daily_briefing.profile import build_daily_briefing_agent_profile
from db import get_conn
from llm_helpers import call_llm

DEFAULT_SIMILAR_LIMIT = 4


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
    local_date = _parse_date(date_value)
    tz = _resolve_timezone(timezone_name)
    start_local = datetime.combine(local_date, time.min).replace(tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

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

    all_todos = todos_service.list_unlinked_relevant_todos()

    context = {
        "date": local_date.isoformat(),
        "timezone": timezone_name,
        "day_start": start_local.isoformat(),
        "day_end": end_local.isoformat(),
        "events": event_contexts,
        "all_todos": all_todos,
    }

    markdown = _generate_markdown(context)
    summary = _generate_summary(context, markdown)
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
            WHERE e.start_date >= %s AND e.start_date < %s
            ORDER BY e.start_date ASC
            """,
            (start, end),
        )
        rows = [dict(row) for row in cur.fetchall()]
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
    return [_normalize_event_row(row) for row in rows]


def _fetch_similar_by_recurrence(
    event_id: str,
    recurrence_key: str,
    day_start: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    if not recurrence_key:
        return []
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
        retry_prompt = _build_rewrite_prompt(context, content)
        content = call_llm(
            retry_prompt,
            system_prompt="Rewrite strictly to the required format.",
            temperature=0.1,
        )
    return content


def _build_briefing_prompt(context: dict[str, Any]) -> str:
    return (
        "DAILY BRIEFING TASK (PREP DOCUMENT)\n"
        "You are preparing the user for upcoming events. Use future tense (will, upcoming, prepare, review).\n"
        "\n"
        "OUTPUT RULES (MANDATORY):\n"
        "- Output Markdown only.\n"
        "- Do not ask questions.\n"
        "- Do not explain the input.\n"
        "- Do not say 'you provided' or 'the text appears'.\n"
        "- Focus ONLY on today's events (and their past context if any) and their linked todos.\n"
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
        "- Past context (from similar events or documents that match this event)\n"
        "- Open actions and pending todos tied to this event\n"
        "- Documents or materials to review for this event\n"
        "- Suggested prep focus for this event\n"
        "## Outstanding Todos\n"
        "- List pending todos and recently completed ones (last 2 weeks).\n"
        "\n"
        "If there are no events, say so in Day Overview and skip Schedule/Event Prep sections.\n"
        "If there are no relevant todos, still include Outstanding Todos with a short note.\n"
        "\n"
        "CONTEXT FOR TODAY (already filtered; every event below is UPCOMING today):\n"
        f"{_format_context_text(context)}"
    )


def _build_rewrite_prompt(context: dict[str, Any], draft: str) -> str:
    return (
        "Rewrite the draft into the REQUIRED STRUCTURE. Output Markdown only.\n"
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
    return call_llm(prompt, system_prompt=system_prompt, temperature=0.2)


def _condense_notes(notes: str, limit: int = 12) -> str:
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
        summary = event.get("summary") or ""
        if summary:
            lines.append("- Context from prior notes (for prep):")
            lines.append(_condense_notes(summary))

        contacts = event.get("contacts") or []
        lines.append(f"- People: {_format_contacts(contacts)}")

        similar = event.get("similar_events") or []
        lines.append(f"- This event has context from {len(similar)} past events. Past event list:")
        if not similar:
            lines.append("  - None")
        for similar_event in similar:
            lines.append(
                "  - "
                f"{similar_event.get('local_start') or similar_event.get('start_date')}"
                f" | {similar_event.get('title')}"
            )

        todos = event.get("todos") or []
        lines.append(f"- Event Todos (linked to this event) ({len(todos)}):")
        if not todos:
            lines.append("  - None")
        for todo in todos:
            lines.append(
                "  - "
                f"[{todo.get('status')}] {todo.get('description')}"
                f" (updated {todo.get('updated_at') or todo.get('created_at')})"
            )

        related_todos = event.get("related_todos") or []
        lines.append(f"- Related Past Event Todos ({len(related_todos)}):")
        if not related_todos:
            lines.append("  - None")
        for todo in related_todos:
            source = todo.get("source_event") or "past event"
            lines.append(
                "  - "
                f"[{todo.get('status')}] {todo.get('description')}"
                f" (from {source}, updated {todo.get('updated_at') or todo.get('created_at')})"
            )
        lines.append(
            "- Prep expectation: summarize past context and propose what the user should do."
        )
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
