"""
Context builders for the bounded agent.

These functions build context that gets injected into prompts:
- Time context (current time, timezone)
- Tag taxonomy (available tags for filtering)
- Self context (user information)
"""

from datetime import datetime, timezone
from typing import Optional


def get_time_context() -> str:
    """Get the current time context for the agent."""
    current_utc = datetime.now(timezone.utc)
    local_now = current_utc.astimezone()
    return (
        "Current time context available to you:\n"
        f"- UTC now: {current_utc.isoformat()}\n"
        f"- Local system time: {local_now.isoformat()}"
    )


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
        print(f"[context] Failed to get tag taxonomy: {exc}")
        return None


def get_self_context(email: str) -> Optional[str]:
    """Get context about the user themselves."""
    if not email:
        return None

    # Try to find the user in contacts
    try:
        import contacts

        user_info = contacts.find_self_contact(email)
        if user_info:
            return f"You are assisting {user_info.get('name', 'the user')}. Their email is {email}."
    except Exception:
        pass

    return f"You are assisting the user with email: {email}"


def get_schema_hint() -> Optional[str]:
    """Get the database schema hint if available."""
    try:
        import sql_tools

        return sql_tools.load_schema_hint()
    except Exception as exc:
        print(f"[context] Failed to load schema hint: {exc}")
        return None


def get_skill_index() -> Optional[str]:
    """Get the lightweight skill index."""
    try:
        import skills

        registry = skills.get_registry()
        return registry.get_skill_index()
    except Exception as exc:
        print(f"[context] Failed to get skill index: {exc}")
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
