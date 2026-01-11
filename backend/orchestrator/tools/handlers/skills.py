"""
Skills-related tool handlers.

Handles:
- run_skill_script: Execute scripts from active skills
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


def handle_run_skill_script(
    args: Dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Execute run_skill_script tool.

    Runs a script from an active skill with provided arguments.
    """
    # Lazy import to avoid circular dependencies
    import skills

    skill_name = args.get("skill_name")
    script_name = args.get("script_name")
    script_args = args.get("args") or {}

    if not skill_name or not isinstance(skill_name, str):
        return {"error": "run_skill_script requires a skill_name string"}
    if not script_name or not isinstance(script_name, str):
        return {"error": "run_skill_script requires a script_name string"}

    # Verify the skill is active (if state is provided)
    if state is not None:
        active_skill_names = [s.get("name") for s in state.activated_skills]
        if skill_name not in active_skill_names:
            return {
                "error": f"Skill '{skill_name}' is not active. Active skills: {active_skill_names}"
            }

    # Get skill and run script
    registry = skills.get_registry()
    skill = registry.get_skill(skill_name)
    if not skill:
        return {"error": f"Skill '{skill_name}' not found in registry"}

    print(
        f"[tool.skills] run_skill_script(skill={skill_name}, "
        f"script={script_name}, args={script_args})"
    )
    step_start = perf_counter()

    runner = skills.get_runner_for_skill(skill.path)
    result = runner.run_script(script_name, script_args)

    # Update state if provided
    if state is not None:
        state.add_action(f"Ran skill script: {skill_name}/{script_name}")

    _log_timing(
        "tool.run_skill_script",
        step_start,
        skill=skill_name,
        script=script_name,
        returncode=result.get("returncode"),
    )
    return result


async def handle_run_skill_script_streaming(
    args: Dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Execute run_skill_script tool with streaming support.

    Returns the result dict with an additional _status_messages key
    containing any status messages emitted during execution.
    """
    # Lazy import to avoid circular dependencies
    import skills

    skill_name = args.get("skill_name")
    script_name = args.get("script_name")
    script_args = args.get("args") or {}

    if not skill_name or not script_name:
        return {"error": "skill_name and script_name are required"}

    # Verify skill is active (if state is provided)
    if state is not None:
        active_skill_names = [s.get("name") for s in state.activated_skills]
        if skill_name not in active_skill_names:
            return {"error": f"Skill '{skill_name}' is not active"}

    registry = skills.get_registry()
    skill = registry.get_skill(skill_name)
    if not skill:
        return {"error": f"Skill '{skill_name}' not found"}

    print(f"[tool.skills] running skill script: {skill_name}/{script_name}")
    step_start = perf_counter()

    runner = skills.get_runner_for_skill(skill.path)
    status_messages = []

    async def collect_status(line: str) -> None:
        if line.startswith("STATUS: "):
            status_messages.append(line[8:])

    result = await runner.run_script_streaming(
        script_name,
        script_args,
        on_output_async=collect_status,
    )

    # Update state if provided
    if state is not None:
        state.add_action(f"Ran skill script: {skill_name}/{script_name}")

    _log_timing(
        "tool.run_skill_script",
        step_start,
        skill=skill_name,
        script=script_name,
    )

    result["_status_messages"] = status_messages
    return result
