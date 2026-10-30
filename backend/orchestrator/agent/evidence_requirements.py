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
    continuation = bool(
        re.search(
            r"^\s*(?:so[?!. ]*$|and[?!. ]*$|then[?!. ]*$|yes\b|no\b|ok(?:ay)?\b|continue\b|go on\b|do it\b)"
            r"|\b(?:participants?|attendees?|original (?:request|question)|you (?:look|check))\b",
            question,
            re.I,
        )
    )
    if not continuation or (meeting_request(question) and _anchors(question)):
        return question
    messages = (history or [])[-12:]
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        content = message_body(str(message.get("content") or ""))
        if (
            message.get("role") == "user"
            and meeting_request(content)
            and re.search(
                r"\b(find|research|prepare|profiles?|public|who|tell|show|list|get)\b",
                content,
                re.I,
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
    )


def _anchors(goal: str) -> list[str]:
    # Topic immediately before/after a meeting reference is stronger than generic
    # research language elsewhere in the sentence. No inferred company/person IDs.
    text = goal.split("\nLatest correction:")[0]
    match = re.search(
        r"\b(?:my|our|the)\s+([\w '&.-]{1,70}?)\s+(?:meeting|call|interview)\b", text, re.I
    )
    if not match:
        match = re.search(
            r"\b(?:meeting|call|interview)\s+(?:about|with|for)\s+([\w &.-]{1,70})", text, re.I
        )
    return topic_terms(match.group(1)) if match else []


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
    anchors = _anchors(goal)
    events = {}
    for call in calls:
        if call.tool_name != "get_events" or not call.success:
            continue
        for event in (call.result or {}).get("events", []):
            text = normalize_search_text(
                str(event.get("title") or "")
                + " "
                + str(event.get("summary") or "")
                + " "
                + " ".join(
                    str(person.get("display_name") or "")
                    for person in event.get("people", [])
                    if isinstance(person, dict)
                )
            )
            if (not anchors or all(term in _tokens(text) for term in anchors)) and _in_window(
                event, bounds
            ):
                events[str(event.get("id"))] = event
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
