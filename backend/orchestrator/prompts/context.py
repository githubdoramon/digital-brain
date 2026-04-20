"""
Context builders for the bounded agent.

These functions build context that gets injected into prompts:
- Time context (current time, timezone)
- Tag taxonomy (available tags for filtering)
- Self context (user information)
"""

import contextlib
from datetime import datetime, timezone
from typing import Any, Optional

from observability.logger import get_runtime_logger
from user_fact_rules import RuleScope

logger = get_runtime_logger(__name__)


def get_time_context() -> str:
    """Get the current time context for the agent."""
    current_utc = datetime.now(timezone.utc)
    return f"Current time context available to you:\n- UTC now: {current_utc.isoformat()}\n"


def get_tag_context() -> Optional[str]:
    """Get the tag taxonomy context for the agent."""
    try:
        # Lazy import to avoid circular dependencies
        import tags_manager

        taxonomy = tags_manager.get_tag_taxonomy()
        if not taxonomy:
            return None

        lines = ["Tag taxonomy for filtering searches:"]
        for major_tag, minor_tags in taxonomy.items():
            if minor_tags:
                minor_list = ", ".join(minor_tags[:10])  # Limit for context size
                if len(minor_tags) > 10:
                    minor_list += f" (and {len(minor_tags) - 10} more)"
                lines.append(f"- {major_tag}: {minor_list}")
            else:
                lines.append(f"- {major_tag}")

        return "\n".join(lines)
    except Exception as exc:
        logger.exception("[context] Failed to get tag taxonomy: %s", exc)
        return None


def get_self_context(email: str) -> Optional[str]:
    """Get context about the user themselves."""
    if not email:
        return None

    behavioral_guidance = (
        "The user is the owner/narrator of this memory graph. "
        "When answering people-ranking or interaction questions (for example, who they met/talked to most), "
        "treat the user as the anchor and return counterpart contacts, not the user themselves, "
        "unless the user explicitly asks about themselves."
    )

    # Try to find the user in contacts
    try:
        import contacts

        user_info = contacts.find_self_contact(email)
        if user_info:
            name = str(user_info.get("display_name") or user_info.get("name") or "the user").strip()

            emails = [
                str(value).strip()
                for value in (user_info.get("emails") or [])
                if str(value).strip()
            ]
            if email not in emails:
                emails.append(email)

            aliases = [
                str(value).strip()
                for value in (user_info.get("aliases") or [])
                if str(value).strip()
            ]

            lines = [f"You are assisting {name}."]
            if emails:
                lines.append(f"Known user emails: {', '.join(emails)}.")
            else:
                lines.append(f"Known user email: {email}.")
            if aliases:
                lines.append(f"Known user aliases: {', '.join(aliases)}.")
            lines.append(behavioral_guidance.strip())
            return " ".join(lines)
    except Exception:
        pass

    return f"You are assisting the user with email: {email}.{behavioral_guidance}"


def get_user_facts_context(
    email: str,
    query: str,
    *,
    scope: RuleScope | str = RuleScope.AGENT_GLOBAL,
) -> Optional[str]:
    """Retrieve deterministic rules + soft facts for prompt injection."""
    if not email:
        return None

    sections: list[str] = []

    try:
        import user_facts

        hard_rules = user_facts.get_hard_rules_context(email, scope=scope)
        if hard_rules:
            sections.append(hard_rules)
    except Exception as exc:
        logger.warning("[context] Failed to get hard user rules: %s", exc)

    try:
        import user_facts

        facts_text = user_facts.get_facts_for_context(email, query)
        if facts_text:
            sections.append(f"Known facts about this user:\n{facts_text}")
    except Exception as exc:
        logger.warning("[context] Failed to get user facts: %s", exc)

    if not sections:
        return None
    return "\n\n".join(sections)


def get_location_context(client_context: Optional[dict[str, Any]]) -> Optional[str]:
    """Format client-provided runtime location/timezone context for prompt injection."""
    if not client_context:
        return None

    timezone_name = str(client_context.get("timezone") or "").strip()
    locale = str(client_context.get("locale") or "").strip()
    location = client_context.get("location")
    inferred_location = client_context.get("inferred_location")
    recent_resolved_place = client_context.get("recent_resolved_place")

    lines: list[str] = ["Client runtime context:"]

    if timezone_name:
        lines.append(f"- Timezone: {timezone_name}")
    if locale:
        lines.append(f"- Locale: {locale}")

    if isinstance(location, dict):
        lat = _as_float(location.get("lat"))
        lon = _as_float(location.get("lon"))
        if lat is not None and lon is not None:
            lines.append(f"- Approximate location: {lat:.3f}, {lon:.3f}")

        accuracy = location.get("accuracy_m")
        if accuracy is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"- Location accuracy: {round(float(accuracy), 1)} meters")

        captured_at = str(location.get("captured_at") or "").strip()
        if captured_at:
            lines.append(f"- Location captured at: {captured_at}")

        source = str(location.get("source") or "").strip()
        if source:
            lines.append(f"- Location source: {source}")

    if isinstance(inferred_location, dict):
        inferred_name = str(inferred_location.get("place_name") or "").strip()
        if inferred_name:
            lines.append(f"- Likely current place: {inferred_name}")

        inferred_city = str(inferred_location.get("city") or "").strip()
        inferred_country = str(inferred_location.get("country") or "").strip()
        if inferred_city or inferred_country:
            locality = ", ".join(part for part in [inferred_city, inferred_country] if part)
            lines.append(f"- Place locality: {locality}")

        inferred_source = str(inferred_location.get("source") or "").strip()
        if inferred_source:
            lines.append(f"- Place inference source: {inferred_source}")

        confidence = str(inferred_location.get("confidence") or "").strip()
        if confidence:
            lines.append(f"- Place confidence: {confidence}")

        distance_m = inferred_location.get("distance_m")
        distance_value = _as_float(distance_m)
        if distance_value is not None:
            lines.append(f"- Distance to inferred place: {round(distance_value, 1)} meters")

    if isinstance(recent_resolved_place, dict):
        recent_place_id = str(recent_resolved_place.get("place_id") or "").strip()
        recent_place_name = str(recent_resolved_place.get("place_name") or "").strip()
        if recent_place_id:
            if recent_place_name:
                lines.append(
                    f"- Recent resolved place reference: {recent_place_name} (place_id: {recent_place_id})"
                )
            else:
                lines.append(f"- Recent resolved place reference: place_id {recent_place_id}")

        recent_address = str(recent_resolved_place.get("address") or "").strip()
        if recent_address:
            lines.append(f"- Recent place address text: {recent_address}")

        recent_role_hint = str(recent_resolved_place.get("role_hint") or "").strip()
        if recent_role_hint:
            lines.append(f"- Recent place role hint: {recent_role_hint}")

        lines.append(
            "- A recent resolved place is available; use its place_id for follow-up place lookups when relevant."
        )

    if len(lines) == 1:
        return None

    lines.append("- Treat coordinates and inferred place as approximate context.")
    return "\n".join(lines)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def get_skill_index() -> Optional[str]:
    """Get the lightweight skill index."""
    try:
        import skills

        registry = skills.get_registry()
        return registry.get_skill_index()
    except Exception as exc:
        logger.exception("[context] Failed to get skill index: %s", exc)
        return None


def format_conversation_history(
    history: list[dict],
    max_messages: int = 20,
) -> list[dict]:
    """
    Format conversation history for the message list.

    Args:
        history: List of message dicts with 'role' and 'content'
        max_messages: Maximum messages to include

    Returns:
        Formatted message list
    """
    if not history:
        return []

    # Take the most recent messages
    recent = history[-max_messages:] if len(history) > max_messages else history

    return [
        {"role": msg["role"], "content": msg["content"]}
        for msg in recent
        if msg.get("role") and msg.get("content")
    ]
