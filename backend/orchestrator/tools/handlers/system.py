"""
System-related tool handlers.

Handles:
- bash: Execute shell commands

Note: Tracing/logging is handled at the controller level via the centralized
trace module. Handlers focus purely on execution logic.
"""

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agent.state import AgentState


def handle_bash(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> dict[str, Any]:
    """
    Execute bash tool.

    Runs a shell command in a sandboxed environment with timeout protection.
    """
    # Lazy import to avoid circular dependencies
    import bash_tools

    command = args.get("command")
    if not command or not isinstance(command, str):
        return {"error": "bash requires a command string"}

    timeout_arg = args.get("timeout")
    try:
        timeout = int(timeout_arg) if timeout_arg is not None else None
    except (TypeError, ValueError):
        timeout = None

    result = bash_tools.execute_bash(command, timeout=timeout)

    # Update state if provided
    if state is not None:
        returncode = result.get("returncode", -1)
        if returncode == 0:
            state.add_action(f"Executed command: {command[:50]}...")
        else:
            state.add_fact(f"Command failed with code {returncode}")

    return result
