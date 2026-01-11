"""
Prompt building and context injection for the LLM.

This module contains:
- System prompts and protocol instructions
- Context builders (tags, self-context, time)
- Message list construction
"""

from datetime import datetime, timezone
from typing import Any, Optional

import skills
import sql_tools
import tags_manager


def get_system_prompt(search_limit: int) -> str:
    """Get the main system prompt for the agent."""
    return (
        "You are a personal memory assistant helping the user explore their stored memories, moments, contacts, events, places and documents. "
        "Be conversational and helpful - make the user feel like they're talking to a knowledgeable friend, not a robot. "
        "Never fabricate information; if no relevant memories exist, say so honestly. "
        "Never expose raw IDs (like contact:1761950388937) - always use human-readable names and titles. "
        f"Prefer returning at most {search_limit} highly relevant results unless the user requests more."
    )


def get_protocol_prompt() -> str:
    """Get the tool usage protocol prompt."""
    return (
        "Tool usage guidelines:\n"
        "- Use `describe_schema` if you need to understand database structure before writing SQL.\n"
        "- Use `search_memories` for semantic/vector search across events and documents.\n"
        "- Use `execute_sql` for precise queries - validate column names against schema, use ILIKE for case-insensitive matching.\n"
        "- Use `resolve_query` to extract contacts, places, and time ranges from natural language.\n"
        "- The database is personal to this user - all data relates to them.\n"
        "- Tasks/todos are in the 'todos' table.\n\n"
        "CRITICAL - Tool calling behavior:\n"
        "- You MUST actually invoke tools using the tool_call mechanism - NEVER just describe what you would do.\n"
        "- WRONG: 'I will search for meetings...' or 'Let me query the database...'\n"
        "- RIGHT: Actually call search_memories or execute_sql with proper arguments.\n"
        "- If you need information from the database, CALL the tool immediately - don't narrate your intentions.\n"
        "- After receiving tool results, synthesize them into a helpful answer.\n\n"
        "Response protocol:\n"
        "- Only provide a final answer to the user when you have ALL the information you need.\n"
        "- If you need to think more, or execute anything before answering to the user, insert a <not_ready> tag in your output.\n"
        "- If you need to call a tool, call it - don't say you will call it.\n"
        "- If a tool returns an error or unexpected result, handle it by trying a different approach.\n"
        "- Your response to the user should be the FINAL answer, not a status update about what you're doing.\n\n"
        "Other behaviors:\n"
        "- If a SQL query fails, revise and retry based on the error.\n"
        "- Respond in the same language the user asked in.\n"
        "- If uncertain, provide your best answer with appropriate caveats rather than refusing.\n\n"
        "For detailed guidance on specific topics (contacts, events, documents), refer to any activated skills below."
    )


def get_event_capture_prompt() -> str:
    """Get the prompt for event capture mode."""
    return (
        "The user is describing something that happened to them or someone they know. "
        "Your job is to extract a precise event record for storage. "
        "When you do not have high confidence about key facts (start time, participants, place, title), "
        "ask concise clarifying questions instead of inventing details. "
        "Once you have enough information, format the event as XML:\n"
        "<event_proposal>\n"
        '{"title": "...", "description": "...", "start_time": "ISO8601", "end_time": "ISO8601", '
        '"location": "...", "attendees": ["name1", "name2"], "all_day": false, "tags": ["tag1"]}\n'
        "</event_proposal>\n"
        "Include this XML block at the end of your response when you have enough information."
    )


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


def build_messages(
    question: str,
    search_limit: int,
    conversation_history: Optional[list[dict[str, str]]] = None,
    user_email: Optional[str] = None,
    current_time_context: Optional[str] = None,
    event_capture_enabled: bool = False,
    state: Optional[Any] = None,  # AgentState
) -> list[dict[str, str]]:
    """
    Build the complete message list for the LLM.

    Args:
        question: The user's question
        search_limit: Max results for searches
        conversation_history: Previous messages in the session
        user_email: User's email for context
        current_time_context: Time context string (or auto-generated)
        event_capture_enabled: Whether to include event capture prompt
        state: AgentState to track activated skills

    Returns:
        List of message dicts for the LLM
    """
    messages: list[dict[str, str]] = []

    # Core system prompts
    messages.append({"role": "system", "content": get_system_prompt(search_limit)})

    # Schema hint
    schema_hint = sql_tools.load_schema_hint()
    if schema_hint:
        messages.append({"role": "system", "content": schema_hint})

    # Tag context
    tags_context = get_tag_context()
    if tags_context:
        messages.append({"role": "system", "content": tags_context})

    # Protocol prompt
    messages.append({"role": "system", "content": get_protocol_prompt()})

    # Self context
    if user_email:
        self_context = get_self_context(user_email)
        if self_context:
            messages.append({"role": "system", "content": self_context})

    # Time context
    time_ctx = current_time_context or get_time_context()
    messages.append({"role": "system", "content": time_ctx})

    # Event capture prompt
    if event_capture_enabled:
        messages.append({"role": "system", "content": get_event_capture_prompt()})

    # Skills integration
    try:
        registry = skills.get_registry()
        print(f"[skills] Registry: {registry}")

        # Always include skill index (lightweight)
        skill_index = registry.get_skill_index()
        if skill_index:
            messages.append({"role": "system", "content": skill_index})

        # Find and inject matching skills
        matching_skills = registry.find_matching_skills(
            query=question,
            conversation_history=conversation_history,
        )
        print(f"[skills] Matching skills: {matching_skills}")

        for match in matching_skills:
            skill_prompt = (
                f"ACTIVE SKILL [{match.skill.name}] (confidence: {match.confidence:.2f}):\n"
                f"{match.skill.instructions}"
            )
            messages.append({"role": "system", "content": skill_prompt})
            print(f"[skills] Activated skill: {match.skill.name} (confidence: {match.confidence:.2f})")

            # Track in state
            if state is not None:
                state.activated_skills.append({
                    "name": match.skill.name,
                    "confidence": match.confidence,
                })
    except Exception as exc:
        print(f"[skills] Error loading skills: {exc}")

    # Conversation history
    if conversation_history:
        for msg in conversation_history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        print(f"[session] Added {len(conversation_history)} messages from session history")

    # User question
    messages.append({"role": "user", "content": question.strip()})

    # Debug output
    import pprint
    messages_str = pprint.pformat(messages)
    if len(messages_str) > 500:
        print("[debug] messages (last 500 chars):", messages_str[-500:])
    else:
        print("[debug] messages:", messages_str)

    return messages
