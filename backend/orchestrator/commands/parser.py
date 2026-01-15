"""
Command parser for extracting commands from user messages.

Handles messages that start with / and extracts:
- Command name (e.g., "new", "event")
- Command arguments (the text after the command)
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedCommand:
    """Represents a parsed command from user input."""

    command: str  # Command name without the /
    args: str  # Arguments after the command
    raw_message: str  # Original message


def is_command(message: str) -> bool:
    """
    Check if a message is a command (starts with /).

    Args:
        message: The user message to check

    Returns:
        True if the message starts with /, False otherwise
    """
    stripped = message.strip()
    return stripped.startswith("/") and len(stripped) > 1


def parse_command(message: str) -> Optional[ParsedCommand]:
    """
    Parse a command from a user message.

    Args:
        message: The user message to parse

    Returns:
        ParsedCommand if the message is a valid command, None otherwise

    Examples:
        >>> parse_command("/new")
        ParsedCommand(command="new", args="", raw_message="/new")

        >>> parse_command("/event met with john at the cafe")
        ParsedCommand(command="event", args="met with john at the cafe", raw_message="/event met with john at the cafe")

        >>> parse_command("not a command")
        None
    """
    stripped = message.strip()

    if not is_command(stripped):
        return None

    # Remove the leading /
    without_slash = stripped[1:]

    # Split on first whitespace to separate command from args
    parts = without_slash.split(maxsplit=1)

    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    return ParsedCommand(
        command=command,
        args=args.strip(),
        raw_message=message,
    )
