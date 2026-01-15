"""
Handler for the /new command.

The /new command creates a new conversation session.
"""

from typing import Any

from commands.parser import ParsedCommand
from commands.registry import CommandRegistry


def handle_new(parsed: ParsedCommand, context: dict) -> dict[str, Any]:
    """
    Handle the /new command.

    Creates a new conversation session and optionally processes a message.

    Args:
        parsed: Parsed command with optional message as args
        context: Context dict with user info

    Returns:
        Dict with:
            - type: "new_session"
            - message: Optional message to process in new session
            - has_message: Whether there's a message to process
    """
    return {
        "type": "new_session",
        "message": parsed.args,
        "has_message": bool(parsed.args),
    }


def register(registry: CommandRegistry) -> None:
    """Register the /new command."""
    registry.register(
        name="new",
        handler=handle_new,
        description="Start a new conversation session",
        requires_args=False,
    )
