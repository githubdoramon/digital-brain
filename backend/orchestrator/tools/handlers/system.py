"""
System-related tool handlers.

Handles:
- bash: Execute shell commands
"""

from time import perf_counter
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.state import AgentState


def _log_timing(label: str, start_time: float, **metadata: Any) -> None:
    """Log timing information for performance monitoring."""
    elapsed_ms = (perf_counter() - start_time) * 1000
    parts = [f"[timing] {label}: {elapsed_ms:.1f}ms"]
    if metadata:
        meta_str = ", ".join(f"{k}={v}" for k, v in metadata.items())
        parts.append(f"({meta_str})")
    print(" ".join(parts))


def handle_bash(
    args: Dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> Dict[str, Any]:
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

    print(f"[tool.system] bash(command={command!r}, timeout={timeout})")
    step_start = perf_counter()

    result = bash_tools.execute_bash(command, timeout=timeout)

    # Update state if provided
    if state is not None:
        returncode = result.get("returncode", -1)
        if returncode == 0:
            state.add_action(f"Executed command: {command[:50]}...")
        else:
            state.add_fact(f"Command failed with code {returncode}")

    _log_timing(
        "tool.bash",
        step_start,
        returncode=result.get("returncode"),
        stdout_len=len(result.get("stdout", "")),
        stderr_len=len(result.get("stderr", "")),
    )
    return result
