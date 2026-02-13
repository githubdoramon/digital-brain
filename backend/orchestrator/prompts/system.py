"""Shared prompt helpers used across bounded agents and tools."""

from agents.main.prompts import get_main_bounded_protocol_prompt, get_main_system_prompt


def get_system_prompt(search_limit: int = 30) -> str:
    """Backward-compatible alias for the main agent system prompt."""
    return get_main_system_prompt(search_limit)


def get_protocol_prompt() -> str:
    """Get the tool usage protocol prompt (original version)."""
    return (
        "Tool usage guidelines:\n"
        "- The database is personal to this user - all data relates to them.\n"
        "- Tasks/todos are in the 'todos' table.\n"
        "- Refer to tool descriptions for specific usage guidance.\n\n"
        "CRITICAL - Tool calling behavior:\n"
        "- You MUST actually invoke tools using the tool_call mechanism - NEVER just describe what you would do.\n"
        "- WRONG: 'I will search for meetings...' or 'Let me query later...'\n"
        "- RIGHT: Actually call search_memories / get_events / get_document with proper arguments.\n"
        "- If you need information, CALL the tool immediately - don't narrate your intentions.\n"
        "- After receiving tool results, synthesize them into a helpful answer.\n\n"
        "Response protocol:\n"
        "- Only provide a final answer to the user when you have ALL the information you need.\n"
        "- If you need to think more, or execute anything before answering to the user, insert a <not_ready> tag in your output.\n"
        "- If you need to call a tool, call it - don't say you will call it.\n"
        "- If a tool returns an error or unexpected result, handle it by trying a different approach.\n"
        "- Your response to the user should be the FINAL answer, not a status update about what you're doing.\n\n"
        "Other behaviors:\n"
        "- If a tool call fails, revise and retry based on the error.\n"
        "- Respond in the same language the user asked in.\n"
        "- If uncertain, provide your best answer with appropriate caveats rather than refusing.\n\n"
        "For detailed guidance on specific topics (contacts, events, documents), refer to any activated skills below."
    )


def get_bounded_agent_protocol() -> str:
    """Backward-compatible alias for the main bounded protocol prompt."""
    return get_main_bounded_protocol_prompt()
