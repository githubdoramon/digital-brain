"""
Bash command execution tool for the LLM agent.

Provides sandboxed shell command execution with timeout protection
and output limits.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

# Default and maximum timeout in seconds
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120

# Maximum output size to prevent memory issues (64KB)
MAX_OUTPUT_SIZE = 65536

# Optional: blocked command patterns for safety
# These are patterns that should not be executed
BLOCKED_PATTERNS = [
    # Destructive file operations
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=",
    # Network scanning/attacks
    "nmap",
    "masscan",
    # Fork bombs and resource exhaustion
    ":(){ :|:& };:",
    # Crypto mining
    "xmrig",
    "minerd",
]


def execute_bash(
    command: str,
    timeout: int | None = None,
    working_dir: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Execute a shell command and return the result.

    Args:
        command: The shell command to execute
        timeout: Maximum execution time in seconds (default 30, max 120)
        working_dir: Working directory for the command (default: current dir)
        env: Additional environment variables to set

    Returns:
        Dict with:
        - stdout: Command standard output (truncated if too large)
        - stderr: Command standard error (truncated if too large)
        - returncode: Exit code (0 = success)
        - error: Error message if execution failed
        - truncated: True if output was truncated
    """
    if not command or not isinstance(command, str):
        return {
            "error": "Command is required and must be a string",
            "stdout": "",
            "stderr": "",
            "returncode": -1,
        }

    command = command.strip()
    if not command:
        return {
            "error": "Command cannot be empty",
            "stdout": "",
            "stderr": "",
            "returncode": -1,
        }

    # Check for blocked patterns
    command_lower = command.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern.lower() in command_lower:
            return {
                "error": f"Command blocked for safety: contains '{pattern}'",
                "stdout": "",
                "stderr": "",
                "returncode": -1,
            }

    # Validate and clamp timeout
    if timeout is None:
        timeout = DEFAULT_TIMEOUT
    else:
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        timeout = max(1, min(timeout, MAX_TIMEOUT))

    # Build environment
    cmd_env = os.environ.copy()
    if env:
        cmd_env.update(env)

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=working_dir,
            env=cmd_env,
        )

        print(result)

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        truncated = False

        # Truncate output if too large
        if len(stdout) > MAX_OUTPUT_SIZE:
            stdout = stdout[:MAX_OUTPUT_SIZE] + "\n... [output truncated]"
            truncated = True
        if len(stderr) > MAX_OUTPUT_SIZE:
            stderr = stderr[:MAX_OUTPUT_SIZE] + "\n... [output truncated]"
            truncated = True

        output = {
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
        }

        if truncated:
            output["truncated"] = True

        # Always print stdout
        if stdout:
            print(stdout)

        # Print debug info on non-zero return code
        if result.returncode != 0:
            print(f"[bash_tools] ERROR: Command exited with return code {result.returncode}")
            print(f"[bash_tools] Command was: {command!r}")
            if stderr:
                print(f"[bash_tools] stderr: {stderr}")

        return output

    except subprocess.TimeoutExpired:
        print(f"[bash_tools] ERROR: Command timed out after {timeout} seconds")
        print(f"[bash_tools] Command was: {command!r}")
        return {
            "error": f"Command timed out after {timeout} seconds",
            "stdout": "",
            "stderr": "",
            "returncode": -1,
        }
    except Exception as e:
        print(f"[bash_tools] ERROR: Command failed with exception: {e}")
        print(f"[bash_tools] Command was: {command!r}")
        print(f"[bash_tools] Working dir: {working_dir}")
        return {
            "error": str(e),
            "stdout": "",
            "stderr": "",
            "returncode": -1,
        }
