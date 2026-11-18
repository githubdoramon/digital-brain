"""Controller-owned prerequisites for personal-event research.

Search activity is discovery, not proof that a requested event or identity was found.
These checks only request reads; they never authorize mutations or select ambiguous people.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from search_normalization import normalize_search_text

_NOISE = {
    "a",
    "an",
    "the",
    "my",
    "our",
    "your",
    "me",
    "we",
    "i",
    "for",
    "of",
    "to",
    "in",
    "on",
    "at",
    "and",
    "or",
    "with",
    "about",
    "today",
    "tomorrow",
    "yesterday",
    "meeting",
    "meetings",
    "call",
    "interview",
    "participants",
    "participant",
    "attendees",
    "attendee",
    "people",
    "calendar",
    "event",
    "events",
    "find",
    "search",
    "information",
    "public",
    "profiles",
    "profile",
    "prepare",
    "preparation",
    "help",
    "please",
    "all",
    "who",
    "are",
    "is",
    "this",
    "that",
    "get",
    "look",
    "up",
    "company",
    "team",
    "emails",
    "email",
    "names",
    "name",
    "have",
    "can",
    "you",
    "it",
    "today's",
    "upcoming",
    "next",
    "scheduled",
    "week",
    "last",
    "previous",
    "past",
    "recent",
    "new",
}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", normalize_search_text(text)))


def topic_terms(text: str) -> list[str]:
    """Content words for lexical discovery; never turn them into contact identities."""
    return list(
        dict.fromkeys(
            w
            for w in re.findall(r"\w+", normalize_search_text(text))
            if len(w) >= 3 and w not in _NOISE and not w.isdigit()
        )
    )[:24]


def message_body(content: str) -> str:
    """Remove the history renderer's timestamp header before interpreting task dates."""
    return re.sub(r"^\[Message sent at [^\n]+\]\n", "", content, count=1)


def evidence_goal(question: str, history: list[dict] | None) -> str:
    """Carry the original task into short follow-ups without using assistant assertions."""
    if re.search(r"\b(forget|instead|ignore|different|another)\b", question, re.I):
        return question
    messages = (history or [])[-12:]
    recap_selection = bool(
        messages and messages[-1].get("role") == "assistant"
        and message_body(str(messages[-1].get("content") or "")).startswith(
            "Which meeting should I summarize?"
        )
        and len(question) <= 200
    )
    continuation = recap_selection or bool(
        re.search(
            r"^\s*(?:so[?!. ]*$|and[?!. ]*$|then[?!. ]*$|yes\b|no\b|ok(?:ay)?\b|continue\b|go on\b|do it\b)"
            r"|\b(?:participants?|attendees?|original (?:request|question)|you (?:look|check))\b",
            question,
            re.I,
        )
    )
    if not continuation or (meeting_request(question) and _anchors(question)):
        return question
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        content = message_body(str(message.get("content") or ""))
        if (
            message.get("role") == "user"
            and (
                (recap_selection and meeting_recap_request(content))
                or (meeting_request(content) and re.search(
                    r"\b(find|research|prepare|profiles?|public|who|tell|show|list|get)\b",
                    content, re.I,
                ))
            )
        ):
            corrections = [
                message_body(str(entry.get("content") or ""))
                for entry in messages[index + 1 :]
                if entry.get("role") == "user"
            ]
            return content + "\nLatest correction: " + "\n".join([*corrections, question])
    return question


def meeting_request(goal: str) -> bool:
    text = goal.lower()
    return bool(
        re.search(r"\b(meeting|call|interview)\b", text)
        and re.search(r"\b(participants?|attendees?|prepare|profiles?)\b", text)
        and not meeting_recap_request(goal)
    )


def meeting_recap_request(goal: str) -> bool:
    """Recognize requests that need a past discussion, not attendee research."""
    goal = goal.split("\nLatest correction:", 1)[0]
    return bool(
        re.search(r"\b(summari[sz]e|summary|recap|what (?:did we discuss|was discussed|happened))\b", goal, re.I)
        and past_recap_clause(goal)
        and not re.search(r"\b(?:participants?|attendees?|profiles?|public|research|send|schedule|update|delete|create)\b", goal, re.I)
    )


def past_recap_clause(goal: str) -> str:
    match = re.search(
        r"\b(?:last|previous|past|most recent)\b[^.!?\n]{0,80}?\b(?:meeting|call|interview)\b[^.!?\n]*",
        goal.split("\nLatest correction:", 1)[0], re.I,
    )
    return match[0] if match else ""


def _topic_groups(goal: str) -> list[set[str]]:
    """Collect a topic beside a meeting reference, allowing slash-separated names."""
    text = goal.split("\nLatest correction:")[0]
    patterns = (
        r"\b(?:meeting|call|interview)\s+(?:about|with|for)\s+([\w '/&-]{1,70}?)"
        r"(?=\s+(?:today|tomorrow|yesterday|this|last|next|to)\b|[^\w '/&-]|$)",
        r"\b(?:my|our|the|last|previous|past|most recent)\s+([\w '/&-]{1,70}?)\s+(?:meeting|call|interview)\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            groups = [set(topic_terms(name)) for name in match[1].split("/")]
            if any(groups):
                return [group for group in groups if group]
    return []


def _anchors(goal: str) -> bool:
    return bool(_topic_groups(goal))


def _event_matches(event: dict, topics: list[set[str]], fields: tuple[str, ...]) -> bool:
    text = " ".join(str(event.get(field) or "") for field in fields if field != "people")
    if "people" in fields:
        text += " " + " ".join(
            str(person.get("display_name") or "")
            for person in event.get("people") or []
            if isinstance(person, dict)
        )
    return not topics or any(group <= _tokens(text) for group in topics)


def recap_candidates(goal: str, calls: list[Any], *, now: str, bounds=None) -> list[dict]:
    """Use inspected past events with a matching title, keeping the latest version per ID."""
    topics = _topic_groups(past_recap_clause(goal)) or _topic_groups(goal)
    if not topics and not bounds:
        return []
    cutoff = datetime.fromisoformat(now)
    events = {}
    for call in calls:
        if call.tool_name != "get_events" or not call.success:
            continue
        for event in call.result.get("events", []):
            if not isinstance(event, dict) or not event.get("id") or event.get("is_cancelled"):
                continue
            try:
                start = datetime.fromisoformat(str(event.get("start_date") or ""))
                if start >= cutoff or (bounds and not _in_window(event, bounds)):
                    continue
            except (TypeError, ValueError):
                continue
            if _event_matches(event, topics, ("title",)) and (
                topics or re.search(r"\b(meeting|call|interview)\b", str(event.get("title")), re.I)
            ):
                previous = events.get(event["id"], {})
                events[event["id"]] = {**previous, **{key: value for key, value in event.items() if value}}
    candidates = list(events.values())
    _, _, correction = goal.partition("\nLatest correction:")
    if correction:
        selected = [event for event in candidates if _tokens(event["title"]) <= _tokens(correction.splitlines()[-1])]
        if selected or not bounds:
            return selected
    if len(candidates) > 1 and len({normalize_search_text(event["title"]) for event in candidates}) == 1:
        dates = [datetime.fromisoformat(event["start_date"]) for event in candidates]
        latest = max(dates)
        return [event for event, date in zip(candidates, dates) if date == latest]
    return candidates


def _in_window(event: dict, bounds: tuple[str, str] | None) -> bool:
    if not bounds:
        return True
    try:
        instant = datetime.fromisoformat(str(event.get("start_date") or "").replace("Z", "+00:00"))
        return datetime.fromisoformat(bounds[0]) <= instant <= datetime.fromisoformat(bounds[1])
    except (ValueError, TypeError):
        return False


def inspect_requirements(
    goal: str, calls: list[Any], *, bounds=None, answer: str = ""
) -> dict[str, Any]:
    """Return missing evidence and concrete read guidance, independent of routed intent."""
    if not meeting_request(goal):
        return {"required": False, "missing": [], "completed": []}
    topics = _topic_groups(goal)
    events = {}
    for call in calls:
        if call.tool_name != "get_events" or not call.success:
            continue
        for event in (call.result or {}).get("events", []):
            if _event_matches(event, topics, ("title", "summary", "people")) and _in_window(event, bounds):
                events[str(event.get("id"))] = event
    title_matches = {key: row for key, row in events.items() if _event_matches(row, topics, ("title",))}
    if title_matches:
        events = title_matches
    result: dict[str, Any] = {
        "required": True,
        "missing": [],
        "completed": [],
        "suggested_tools": ["get_events"],
    }
    if len(events) != 1:
        result["missing"] = [
            "Identify the requested meeting with get_events(action=by_time_span)"
            + (
                f" using time_start={bounds[0]}, time_end={bounds[1]}"
                if bounds
                else " using the request's date bounds"
            )
            + "; inspect its linked participants. "
            + (
                "Multiple matching events require clarification; do not choose arbitrarily."
                if events
                else "Refine the topic/date if needed; company contacts or web results do not establish attendance."
            )
        ]
        return result
    event = next(iter(events.values()))
    result["completed"].append("Identify requested meeting")
    people = [person for person in event.get("people", []) if isinstance(person, dict)]
    expected = re.findall(
        r"\b(\d+|two|three|four|five|six|seven|eight|nine|ten)\s+(?:participants|attendees)\b",
        goal,
        re.I,
    )
    number = expected[-1].lower() if expected else "0"
    expected_count = (
        int(number)
        if number.isdigit()
        else {
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
        }[number]
    )
    if not people or len(people) < expected_count:
        result["missing"] = [
            "Inspect the meeting's full participant links; the retrieved attendee list is missing or smaller than the user's stated count. Report the mismatch rather than substituting company team members."
        ]
        return result
    result["completed"].append("Inspect linked participants")
    if not re.search(r"\b(public|online|web|profiles?|research)\b", goal, re.I):
        return result
    # Research must be tied to actual attendee identities, not company-wide queries.
    missing = []
    for person in people:
        if person.get("is_self"):
            continue
        name = str(person.get("display_name") or "")
        emails = person.get("emails") or []
        identities = [
            normalize_search_text(name),
            *(normalize_search_text(str(email)) for email in emails),
        ]
        identities = [
            identity
            for identity in identities
            if identity and identity != normalize_search_text(str(person.get("contact_id") or ""))
        ]
        attempted = any(
            call.tool_name == "web_search"
            and not (call.result or {}).get("_prerequisite_blocked")
            and any(
                _tokens(identity) <= _tokens(str(call.arguments.get("query") or ""))
                for identity in identities
            )
            for call in calls
        )
        covered_in_answer = not answer or any(
            _tokens(identity) <= _tokens(answer) for identity in identities
        )
        if not attempted or not covered_in_answer:
            missing.append(name or "unresolved participant")
    if missing:
        result["missing"] = [
            "Research these linked participants by their verified names/emails, preserving uncertainty for unavailable profiles: "
            + ", ".join(missing)
        ]
        result["suggested_tools"] = ["lookup_contact", "web_search"]
    else:
        result["completed"].append("Research linked participants")
    return result
