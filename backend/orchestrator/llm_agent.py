"""
Agent loop logic for the LLM.

This module contains:
- Continuation detection (shared between streaming and non-streaming)
- Response finalization helpers
- Agent loop configuration
"""

import json
import re
from typing import TYPE_CHECKING, Any, Optional

from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

if TYPE_CHECKING:
    from agent.state import AgentState

# Configuration
MAX_ITERATIONS = 15  # Safety limit to prevent infinite loops
MAX_CONTINUATION_RETRIES = 3  # Max times to nudge the model to call tools

# Patterns that indicate the model wants to continue but didn't call a tool
CONTINUATION_PATTERNS = [
    "<not_ready>",
    "<thinking>",
    "let me try",
    "let me find",
    "let me search",
    "let me check",
    "let me look",
    "let me query",
    "let me list",
    "let me get",
    "i need to",
    "i will try",
    "i'll try",
    "i will search",
    "i'll search",
    "i will query",
    "i'll query",
    "i will look",
    "i'll look",
    "i will list",
    "i'll list",
    "i will get",
    "i'll get",
    "first, i need",
    "first i need",
    "i should",
    "i'll need to",
    "i will need to",
    "i would use",
    "i can use",
    "searching for",
    "querying the",
    "to find this, i",
    "to answer this, i",
]

# Max content length to check for continuation patterns
# Longer responses are assumed to be final answers
MAX_CONTINUATION_CHECK_LENGTH = 800


def looks_like_continuation(content: str) -> bool:
    """
    Check if content indicates the model wants to continue but didn't call a tool.

    This detects cases like:
    - "Let me try to find the correct tool..."
    - "I will search for meetings..."
    - "First, I need to list the available tools..."

    Args:
        content: The model's response content

    Returns:
        True if the content looks like the model wants to continue
    """
    lower = content.lower().strip()

    # Only check reasonably short responses
    # Longer responses are likely complete answers
    if len(lower) > MAX_CONTINUATION_CHECK_LENGTH:
        return False

    return any(pattern in lower for pattern in CONTINUATION_PATTERNS)


def create_continuation_nudge() -> dict[str, str]:
    """
    Create a system message to nudge the model to actually call a tool.

    Returns:
        A system message dict
    """
    return {
        "role": "system",
        "content": (
            "You expressed intent to perform an action but didn't actually call a tool. "
            "Please INVOKE the appropriate tool now using the tool_call mechanism. "
            "Don't describe what you'll do - just call the tool directly."
        ),
    }


def create_thinking_nudge() -> dict[str, str]:
    """
    Create a system message to nudge the model to provide a final answer.

    Returns:
        A system message dict
    """
    return {
        "role": "system",
        "content": (
            "Reminder: provide the final answer for the user in natural language "
            "without exposing internal reasoning."
        ),
    }


def is_only_thinking(content: str) -> bool:
    """
    Check if content is only internal thinking without a real answer.

    Args:
        content: The model's response content

    Returns:
        True if the content appears to be only internal reasoning
    """
    stripped = content.strip()
    if not stripped:
        return True

    # Check for thinking tags
    if stripped.startswith("<think>") or stripped.startswith("<thinking>"):
        # Remove thinking tags and check if anything meaningful remains
        cleaned = re.sub(r"</?think(?:ing)?>", "", stripped).strip()
        return len(cleaned) < 10

    return False


# Event proposal extraction
EVENT_PROPOSAL_START = "<event_proposal>"
EVENT_PROPOSAL_END = "</event_proposal>"


def extract_event_proposal(content: str) -> Optional[dict[str, Any]]:
    """
    Extract an event proposal from the model's response.

    Args:
        content: The model's response content

    Returns:
        The extracted event proposal dict, or None if not found
    """
    start_idx = content.find(EVENT_PROPOSAL_START)
    if start_idx == -1:
        return None

    end_idx = content.find(EVENT_PROPOSAL_END, start_idx)
    if end_idx == -1:
        return None

    json_str = content[start_idx + len(EVENT_PROPOSAL_START) : end_idx].strip()

    try:
        raw = json.loads(json_str)
        return normalize_event_proposal(raw)
    except json.JSONDecodeError as exc:
        logger.warning("[agent] Failed to parse event proposal JSON: %s", exc)
        return None


def normalize_event_proposal(raw: Any) -> Optional[dict[str, Any]]:
    """
    Normalize an event proposal to a consistent format.

    Args:
        raw: The raw parsed JSON

    Returns:
        Normalized event proposal dict, or None if invalid
    """
    if not isinstance(raw, dict):
        return None

    # Required fields
    title = raw.get("title")
    if not title or not isinstance(title, str):
        return None

    proposal: dict[str, Any] = {"title": title.strip()}

    # Optional fields with type coercion
    if "description" in raw and raw["description"]:
        proposal["description"] = str(raw["description"]).strip()

    if "start_time" in raw and raw["start_time"]:
        proposal["start_time"] = str(raw["start_time"])

    if "end_time" in raw and raw["end_time"]:
        proposal["end_time"] = str(raw["end_time"])

    if "location" in raw and raw["location"]:
        proposal["location"] = str(raw["location"]).strip()

    if "attendees" in raw and isinstance(raw["attendees"], list):
        proposal["attendees"] = [str(a).strip() for a in raw["attendees"] if a]

    if "all_day" in raw:
        proposal["all_day"] = _coerce_bool(raw["all_day"])

    if "tags" in raw and isinstance(raw["tags"], list):
        proposal["tags"] = [str(t).strip() for t in raw["tags"] if t]

    return proposal


def _coerce_bool(value: Any) -> bool:
    """Coerce a value to boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "yes", "1")
    return bool(value)


def strip_event_proposal(content: str) -> str:
    """
    Remove the event proposal XML from the content.

    Args:
        content: The model's response content

    Returns:
        Content with event proposal removed
    """
    start_idx = content.find(EVENT_PROPOSAL_START)
    if start_idx == -1:
        return content

    end_idx = content.find(EVENT_PROPOSAL_END, start_idx)
    if end_idx == -1:
        return content

    # Remove the event proposal section
    before = content[:start_idx].rstrip()
    after = content[end_idx + len(EVENT_PROPOSAL_END) :].lstrip()

    return (before + " " + after).strip() if after else before


def finalize_bundle(
    question: str,
    answer: str,
    state: "AgentState",
    search_limit: int,
    session_id: Optional[str],
) -> dict[str, Any]:
    """
    Create the final response bundle.

    Args:
        question: The original question
        answer: The final answer text
        state: The agent state
        search_limit: Max number of search rows to include in the bundle
        session_id: The session/thread ID

    Returns:
        The response bundle dict
    """
    search_results: list[dict[str, Any]] = []
    events_results: list[dict[str, Any]] = []
    document_results: list[dict[str, Any]] = []
    seen_document_ids: set[str] = set()

    def _compact_document(document: dict[str, Any]) -> dict[str, Any]:
        raw_metadata = (
            document.get("raw_metadata")
            if isinstance(document.get("raw_metadata"), dict)
            else {}
        )
        preview_source = (
            document.get("content_preview")
            or raw_metadata.get("content_english_for_embedding")
            or raw_metadata.get("original_content")
            or ""
        )
        preview_text = str(preview_source or "").strip()
        if len(preview_text) > 12000:
            preview_text = preview_text[:11997].rstrip() + "..."

        compact: dict[str, Any] = {
            "document_id": document.get("document_id"),
            "title": document.get("title"),
            "tags": document.get("tags"),
            "document_date": document.get("document_date"),
            "file_name": document.get("file_name"),
            "file_mime": document.get("file_mime"),
            "file_size": document.get("file_size"),
            "snippet": document.get("snippet"),
        }
        if preview_text:
            compact["content_preview"] = preview_text
        return compact

    for call in state.tool_calls:
        if call.tool_name == "search_memories" and call.success:
            rows = (call.result or {}).get("results", [])
            if isinstance(rows, list):
                search_results = rows
        elif call.tool_name == "get_events" and call.success:
            events = (call.result or {}).get("events", [])
            if isinstance(events, list):
                events_results.extend(events)
        elif call.tool_name == "get_document" and call.success:
            document = (call.result or {}).get("document")
            if not isinstance(document, dict):
                continue
            compact = _compact_document(document)
            document_id = str(compact.get("document_id") or "").strip()
            dedupe_key = document_id or json.dumps(compact, sort_keys=True, default=str)
            if dedupe_key in seen_document_ids:
                continue
            seen_document_ids.add(dedupe_key)
            document_results.append(compact)

    normalized_limit = 30
    try:
        parsed_limit = int(search_limit)
        if parsed_limit > 0:
            normalized_limit = parsed_limit
    except (TypeError, ValueError):
        pass
    if len(search_results) > normalized_limit:
        search_results = search_results[:normalized_limit]

    bundle: dict[str, Any] = {
        "question": question,
        "answer": answer,
        "thread_id": session_id,
        # Required fields from AgentState
        "resolution": state.resolution,
        "search_results": search_results,
        "events_results": events_results,
        "document_results": document_results,
    }

    if state.activated_skills:
        bundle["activated_skills"] = [s.get("name") for s in state.activated_skills]

    return bundle
