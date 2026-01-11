"""
Prompt management for the bounded agent.

Provides:
- System prompts and protocol instructions
- Context builders (time, tags, self)
- State injection into every call
- Message list construction
"""

from .system import (
    get_system_prompt,
    get_protocol_prompt,
    get_event_capture_prompt,
    get_bounded_agent_protocol,
)
from .context import (
    get_time_context,
    get_tag_context,
    get_self_context,
)
from .state_injection import (
    inject_state_context,
    build_state_message,
)

__all__ = [
    "get_system_prompt",
    "get_protocol_prompt",
    "get_event_capture_prompt",
    "get_bounded_agent_protocol",
    "get_time_context",
    "get_tag_context",
    "get_self_context",
    "inject_state_context",
    "build_state_message",
]
