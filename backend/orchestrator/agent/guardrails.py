"""Controller guardrail helpers for query shaping and scoped context injection."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


def sanitize_goal_text(text: str) -> str:
    """Normalize slash-prefixed command wrappers from user text."""
    stripped = str(text or "").strip()
    return re.sub(r"^/\w+\s+", "", stripped)


def tokenize_text(text: str) -> list[str]:
    """Tokenize text into lowercase lexical tokens."""
    return re.findall(r"[A-Za-z0-9']+", str(text or "").lower())


def extract_contact_scope_terms(active_scope: list[dict[str, Any]]) -> tuple[list[str], set[str]]:
    """Extract phrase and token forms for resolved contact mentions."""
    phrases: list[str] = []
    tokens: set[str] = set()

    for entry in active_scope:
        if not isinstance(entry, dict):
            continue
        for field in ("mention_text", "display_name"):
            raw_value = str(entry.get(field) or "").strip()
            if not raw_value:
                continue
            phrases.append(raw_value)
            for token in tokenize_text(raw_value):
                tokens.add(token)

    deduped_phrases = list(dict.fromkeys(phrases))
    return deduped_phrases, tokens


def optimize_query_for_scoped_contacts(
    query_text: str,
    goal_text: str,
    active_scope: list[dict[str, Any]],
) -> str:
    """
    Keep query focused on semantic topic when contact_ids already scope the person.

    Example:
    - "when did I last meet Gio?" + contact_ids => "events"
    - "when did I last meet Gio and we talked about birds?" => "birds"
    """
    base_query = (query_text or goal_text or "").strip()
    if not base_query:
        return "events"

    phrases, mention_tokens = extract_contact_scope_terms(active_scope)
    scrubbed = base_query
    for phrase in phrases:
        escaped = re.escape(phrase.strip())
        if not escaped:
            continue
        escaped = escaped.replace(r"\ ", r"\s+")
        scrubbed = re.sub(rf"\b{escaped}\b", " ", scrubbed, flags=re.IGNORECASE)

    stop_words = {
        "a",
        "an",
        "and",
        "about",
        "did",
        "do",
        "does",
        "event",
        "events",
        "first",
        "i",
        "last",
        "latest",
        "meet",
        "meeting",
        "meetings",
        "met",
        "most",
        "recent",
        "talk",
        "talked",
        "the",
        "time",
        "was",
        "we",
        "when",
        "where",
        "who",
        "with",
    }
    semantic_terms: list[str] = []
    seen_terms: set[str] = set()
    for token in tokenize_text(scrubbed):
        if token in mention_tokens or token in stop_words:
            continue
        if token not in seen_terms:
            seen_terms.add(token)
            semantic_terms.append(token)

    if semantic_terms:
        return " ".join(semantic_terms)
    return "events"


def build_contact_scope_context(
    active_scope: list[dict[str, Any]],
    resolution_state: dict[str, Any] | None = None,
) -> str | None:
    """Build explicit resolver context for the model when contact scope exists.

    Also informs the model when pre-resolution was already attempted but found
    no existing contacts, so it does not redundantly call ``resolve_contacts``.
    """
    if active_scope:
        lines = ["RESOLVED CONTACT SCOPE (controller authoritative mapping):"]
        for entry in active_scope[:8]:
            mention = str(entry.get("mention_text") or "").strip() or "<unknown mention>"
            display_name = str(entry.get("display_name") or "").strip() or "<unknown contact>"
            contact_id = str(entry.get("contact_id") or "").strip()
            lines.append(f"- '{mention}' -> '{display_name}' (contact_id: {contact_id})")

        lines.extend(
            [
                "",
                "When calling `search_memories` with scoped contacts:",
                "- Always pass the mapped IDs via `contact_ids`.",
                "- Use `query` only for extra semantic topic terms (for example, 'birds').",
                "- Do not repeat resolved person names in `query` unless the name itself is the topic.",
                "- If no extra semantic topic exists, set query to 'events'.",
            ]
        )
        return "\n".join(lines)

    # Pre-resolution was attempted but no existing contacts matched.
    if resolution_state and resolution_state.get("pre_resolution_attempted"):
        people = resolution_state.get("pre_resolution_people") or []
        new_names = resolution_state.get("pre_resolution_new_contacts") or []
        lines = [
            "PRE-RESOLUTION ALREADY ATTEMPTED (do NOT call resolve_contacts again for these):",
        ]
        if people:
            lines.append(f"- People mentioned: {', '.join(str(p) for p in people)}")
        if new_names:
            lines.append(
                f"- No existing contacts found for: {', '.join(new_names)}. "
                "These are new/unknown to the contact directory."
            )
        lines.extend(
            [
                "",
                "Since these people are not in the contact database, use `lookup_contact` "
                "or `search_memories` to find information about them without contact_ids.",
                "Do NOT call resolve_contacts again for the same names.",
            ]
        )
        return "\n".join(lines)

    return None


def detect_temporal_sort_order(query: str) -> str | None:
    """Infer temporal ordering intent from query text."""
    if not query:
        return None

    q = query.lower()
    oldest_patterns = [
        r"\bfirst time\b",
        r"\bfirst meeting\b",
        r"\bfirst event\b",
        r"\bearliest\b",
        r"\bwhen did i first\b",
    ]
    newest_patterns = [
        r"\bmost recent\b",
        r"\blatest\b",
        r"\blast time\b",
        r"\blast meeting\b",
        r"\blast event\b",
        r"\bwhen did i last\b",
    ]

    if any(re.search(pattern, q) for pattern in oldest_patterns):
        return "oldest"
    if any(re.search(pattern, q) for pattern in newest_patterns):
        return "newest"
    return None


def detect_future_temporal_intent(query: str) -> bool:
    """Detect whether the user is asking about future/scheduled events."""
    if not query:
        return False

    q = query.lower()
    future_patterns = [
        r"\bupcoming\b",
        r"\bscheduled\b",
        r"\bin the future\b",
        r"\bfuture\b",
        r"\bnext\b",
        r"\bwill\b",
        r"\bgoing to\b",
        r"\bplan(?:ned)?\b",
        r"\btomorrow\b",
        r"\bthis weekend\b",
        r"\bnext week\b",
        r"\bnext month\b",
    ]
    return any(re.search(pattern, q) for pattern in future_patterns)


def utc_now_iso() -> str:
    """Current UTC timestamp in ISO format for time guardrails."""
    return datetime.now(timezone.utc).isoformat()
