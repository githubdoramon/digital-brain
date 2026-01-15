"""
Command system for handling special user commands like /new, /event, etc.

This module provides infrastructure for parsing and routing commands that start with /.
Commands bypass the normal LLM flow and execute specialized handlers.
"""

from .parser import parse_command, is_command
from .registry import CommandRegistry, get_command_registry

__all__ = [
    "parse_command",
    "is_command",
    "CommandRegistry",
    "get_command_registry",
]
