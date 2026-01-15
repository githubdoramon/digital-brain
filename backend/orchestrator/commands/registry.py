"""
Command registry for managing and executing commands.

The registry maps command names to their handlers and provides
a central place to register new commands.
"""

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .parser import ParsedCommand


@dataclass
class CommandDefinition:
    """Defines a command with its handler and metadata."""

    name: str
    handler: Callable[[ParsedCommand, dict], dict[str, Any]]
    description: str
    requires_args: bool = False


class CommandRegistry:
    """Registry for command handlers."""

    def __init__(self):
        self._commands: dict[str, CommandDefinition] = {}

    def register(
        self,
        name: str,
        handler: Callable[[ParsedCommand, dict], dict[str, Any]],
        description: str,
        requires_args: bool = False,
    ) -> None:
        """
        Register a command handler.

        Args:
            name: Command name (without the /)
            handler: Function that handles the command
            description: Human-readable description of what the command does
            requires_args: Whether the command requires arguments
        """
        self._commands[name.lower()] = CommandDefinition(
            name=name.lower(),
            handler=handler,
            description=description,
            requires_args=requires_args,
        )

    def get(self, name: str) -> Optional[CommandDefinition]:
        """
        Get a command definition by name.

        Args:
            name: Command name (without the /)

        Returns:
            CommandDefinition if found, None otherwise
        """
        return self._commands.get(name.lower())

    def list_commands(self) -> list[CommandDefinition]:
        """
        Get all registered commands.

        Returns:
            List of all command definitions
        """
        return list(self._commands.values())

    def execute(
        self,
        parsed: ParsedCommand,
        context: dict,
    ) -> dict[str, Any]:
        """
        Execute a command.

        Args:
            parsed: Parsed command from user input
            context: Context dict containing user info, etc.

        Returns:
            Command result dict

        Raises:
            ValueError: If command not found or validation fails
        """
        cmd_def = self.get(parsed.command)

        if cmd_def is None:
            raise ValueError(f"Unknown command: /{parsed.command}")

        if cmd_def.requires_args and not parsed.args:
            raise ValueError(f"Command /{parsed.command} requires arguments")

        return cmd_def.handler(parsed, context)


# Global registry instance
_registry: Optional[CommandRegistry] = None


def get_command_registry() -> CommandRegistry:
    """Get the global command registry, creating it if needed."""
    global _registry
    if _registry is None:
        _registry = CommandRegistry()
        # Import and register commands
        from .handlers import event, new

        new.register(_registry)
        event.register(_registry)

    return _registry
