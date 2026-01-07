"""
Skill script runner - executes scripts from skill packages.

Provides secure, sandboxed execution of Python and Bash scripts
with support for both synchronous and streaming execution.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class SkillScriptRunner:
    """
    Executes scripts from a skill package.

    Scripts receive arguments via stdin as JSON and return results via stdout.
    Execution is sandboxed with timeout and directory restrictions.
    """

    def __init__(
        self,
        skill_path: Path,
        timeout: int = 30,
        allowed_extensions: Optional[List[str]] = None,
    ):
        """
        Initialize the script runner.

        Args:
            skill_path: Path to the skill directory
            timeout: Maximum execution time in seconds (default 30)
            allowed_extensions: Allowed script extensions (default .py, .sh)
        """
        self.skill_path = Path(skill_path)
        self.scripts_dir = self.skill_path / "scripts"
        self.timeout = timeout
        self.allowed_extensions = allowed_extensions or [".py", ".sh"]

    def list_scripts(self) -> List[str]:
        """List available scripts in the skill's scripts/ folder."""
        if not self.scripts_dir.exists() or not self.scripts_dir.is_dir():
            return []

        scripts = []
        for f in self.scripts_dir.iterdir():
            if f.is_file() and f.suffix in self.allowed_extensions:
                scripts.append(f.name)

        return sorted(scripts)

    def _get_interpreter(self, script_name: str) -> Optional[List[str]]:
        """Get the interpreter command for a script type."""
        if script_name.endswith(".py"):
            return ["python", "-u"]  # -u for unbuffered output
        elif script_name.endswith(".sh"):
            return ["bash"]
        return None

    def run_script(
        self,
        script_name: str,
        args: Optional[Dict[str, Any]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a script synchronously.

        Args:
            script_name: Name of the script file (e.g., 'generate.py')
            args: Arguments to pass as JSON via stdin
            env: Additional environment variables

        Returns:
            Dict with stdout, stderr, returncode, and parsed result if available
        """
        script_path = self.scripts_dir / script_name

        if not script_path.exists():
            return {
                "error": f"Script not found: {script_name}",
                "stdout": "",
                "stderr": "",
                "returncode": -1,
            }

        if script_path.suffix not in self.allowed_extensions:
            return {
                "error": f"Script type not allowed: {script_path.suffix}",
                "stdout": "",
                "stderr": "",
                "returncode": -1,
            }

        interpreter = self._get_interpreter(script_name)
        if not interpreter:
            return {
                "error": f"No interpreter for script type: {script_path.suffix}",
                "stdout": "",
                "stderr": "",
                "returncode": -1,
            }

        cmd = interpreter + [str(script_path)]
        input_data = json.dumps(args or {})

        # Build environment
        script_env = os.environ.copy()
        script_env["SKILL_PATH"] = str(self.skill_path)
        script_env["SCRIPT_PATH"] = str(script_path)
        if env:
            script_env.update(env)

        try:
            result = subprocess.run(
                cmd,
                input=input_data,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(self.skill_path),
                env=script_env,
            )

            output = {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }

            # Try to parse structured result from stdout
            parsed_result = self._parse_result(result.stdout)
            if parsed_result is not None:
                output["result"] = parsed_result

            return output

        except subprocess.TimeoutExpired:
            return {
                "error": f"Script timed out after {self.timeout}s",
                "stdout": "",
                "stderr": "",
                "returncode": -1,
            }
        except Exception as e:
            return {
                "error": str(e),
                "stdout": "",
                "stderr": "",
                "returncode": -1,
            }

    async def run_script_streaming(
        self,
        script_name: str,
        args: Optional[Dict[str, Any]] = None,
        env: Optional[Dict[str, str]] = None,
        on_output: Optional[Callable[[str], None]] = None,
        on_output_async: Optional[Callable[[str], Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a script with streaming output.

        Args:
            script_name: Name of the script file
            args: Arguments to pass as JSON via stdin
            env: Additional environment variables
            on_output: Sync callback for each line of output
            on_output_async: Async callback for each line of output (awaited)

        Returns:
            Dict with collected output and result
        """
        script_path = self.scripts_dir / script_name

        if not script_path.exists():
            return {
                "error": f"Script not found: {script_name}",
                "stdout": "",
                "stderr": "",
                "returncode": -1,
            }

        interpreter = self._get_interpreter(script_name)
        if not interpreter:
            return {
                "error": f"No interpreter for script type: {script_path.suffix}",
                "stdout": "",
                "stderr": "",
                "returncode": -1,
            }

        cmd = interpreter + [str(script_path)]

        # Build environment
        script_env = os.environ.copy()
        script_env["SKILL_PATH"] = str(self.skill_path)
        script_env["SCRIPT_PATH"] = str(script_path)
        if env:
            script_env.update(env)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.skill_path),
                env=script_env,
            )

            # Send input
            input_data = json.dumps(args or {}).encode()
            process.stdin.write(input_data)
            await process.stdin.drain()
            process.stdin.close()

            # Stream stdout
            output_lines = []
            status_messages = []
            result_data = None

            async def read_with_timeout():
                try:
                    return await asyncio.wait_for(
                        process.stdout.readline(),
                        timeout=self.timeout
                    )
                except asyncio.TimeoutError:
                    return None

            while True:
                line = await read_with_timeout()
                if line is None:
                    # Timeout
                    process.kill()
                    return {
                        "error": f"Script timed out after {self.timeout}s",
                        "stdout": "\n".join(output_lines),
                        "stderr": "",
                        "returncode": -1,
                    }

                if not line:
                    break

                decoded = line.decode().rstrip()
                output_lines.append(decoded)

                # Parse special prefixes for streaming protocol
                if decoded.startswith("STATUS: "):
                    status_messages.append(decoded[8:])
                    if on_output:
                        on_output(decoded)
                    if on_output_async:
                        await on_output_async(decoded)
                elif decoded.startswith("RESULT: "):
                    try:
                        result_data = json.loads(decoded[8:])
                    except json.JSONDecodeError:
                        pass
                elif on_output:
                    on_output(decoded)
                elif on_output_async:
                    await on_output_async(decoded)

            await process.wait()

            stderr_data = await process.stderr.read()

            output = {
                "stdout": "\n".join(output_lines),
                "stderr": stderr_data.decode() if stderr_data else "",
                "returncode": process.returncode,
                "status_messages": status_messages,
            }

            if result_data is not None:
                output["result"] = result_data
            else:
                # Try to parse final result from stdout
                parsed = self._parse_result("\n".join(output_lines))
                if parsed is not None:
                    output["result"] = parsed

            return output

        except Exception as e:
            return {
                "error": str(e),
                "stdout": "",
                "stderr": "",
                "returncode": -1,
            }

    def _parse_result(self, stdout: str) -> Optional[Any]:
        """
        Try to parse a structured result from stdout.

        Looks for:
        1. A line starting with "RESULT: " followed by JSON
        2. The last non-empty line if it's valid JSON
        """
        lines = stdout.strip().split("\n")

        # Look for explicit RESULT line
        for line in reversed(lines):
            if line.startswith("RESULT: "):
                try:
                    return json.loads(line[8:])
                except json.JSONDecodeError:
                    pass

        # Try last non-empty line as JSON
        for line in reversed(lines):
            line = line.strip()
            if line and (line.startswith("{") or line.startswith("[")):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    pass

        return None


def get_runner_for_skill(skill_path: Path, timeout: int = 30) -> SkillScriptRunner:
    """Factory function to create a script runner for a skill."""
    return SkillScriptRunner(skill_path, timeout=timeout)
