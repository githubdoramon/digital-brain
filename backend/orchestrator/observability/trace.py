"""
Centralized tracing/logging for agent execution.

Provides structured, consistent logging across all agent components:
- Router (intent classification)
- Controller (orchestration decisions)
- Validators (pre/post execution)
- Tool handlers (execution)

All output follows a consistent format:
[component.subcomponent] message
"""

import json
import os
from dataclasses import dataclass
from enum import Enum
from time import perf_counter
from typing import Any, Optional


class LogLevel(str, Enum):
    """Log levels for filtering."""
    DEBUG = "debug"
    INFO = "info"
    DECISION = "decision"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class TraceConfig:
    """Configuration for tracing."""
    enabled: bool = True
    min_level: LogLevel = LogLevel.INFO
    show_timestamps: bool = False
    truncate_length: int = 150

    @classmethod
    def from_env(cls) -> "TraceConfig":
        return cls(
            enabled=os.getenv("AGENT_TRACE_ENABLED", "true").lower() == "true",
            min_level=LogLevel(os.getenv("AGENT_TRACE_LEVEL", "info").lower()),
            show_timestamps=os.getenv("AGENT_TRACE_TIMESTAMPS", "false").lower() == "true",
            truncate_length=int(os.getenv("AGENT_TRACE_TRUNCATE", "150")),
        )


# Global config
_config: Optional[TraceConfig] = None


def get_config() -> TraceConfig:
    """Get trace configuration."""
    global _config
    if _config is None:
        _config = TraceConfig.from_env()
    return _config


def _should_log(level: LogLevel) -> bool:
    """Check if we should log at this level."""
    config = get_config()
    if not config.enabled:
        return False

    level_order = [LogLevel.DEBUG, LogLevel.INFO, LogLevel.DECISION, LogLevel.WARNING, LogLevel.ERROR]
    return level_order.index(level) >= level_order.index(config.min_level)


def _truncate(text: str, max_len: Optional[int] = None) -> str:
    """Truncate text if too long."""
    max_len = max_len or get_config().truncate_length
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _format_args(args: dict[str, Any]) -> str:
    """Format arguments for logging."""
    try:
        return _truncate(json.dumps(args, default=str))
    except Exception:
        return str(args)[:100]


# =============================================================================
# ROUTER LOGGING
# =============================================================================

def trace_router_start(question: str) -> float:
    """Log router classification start. Returns start time."""
    if _should_log(LogLevel.INFO):
        q_preview = _truncate(question.replace("\n", " "), 80)
        print(f"[router] Classifying intent: \"{q_preview}\"")
    return perf_counter()


def trace_router_rule_match(
    intent: str,
    confidence: float,
    reasoning: str,
    tool_groups: list[str],
    duration_ms: float,
) -> None:
    """Log successful rule-based classification."""
    if _should_log(LogLevel.DECISION):
        print(f"[router.rule] ✓ Matched: {intent} (confidence={confidence:.2f})")
        print(f"[router.rule]   Reasoning: {reasoning}")
        print(f"[router.rule]   Tool groups: {tool_groups}")
        print(f"[router.rule]   Duration: {duration_ms:.1f}ms")


def trace_router_llm_start() -> float:
    """Log LLM classification start. Returns start time."""
    if _should_log(LogLevel.INFO):
        print("[router.llm] Rule confidence too low, using LLM...")
    return perf_counter()


def trace_router_llm_result(
    intent: str,
    confidence: float,
    reasoning: Optional[str],
    tool_groups: list[str],
    duration_ms: float,
) -> None:
    """Log LLM classification result."""
    if _should_log(LogLevel.DECISION):
        print(f"[router.llm] ✓ Classified: {intent} (confidence={confidence:.2f}, {duration_ms:.0f}ms)")
        if reasoning:
            print(f"[router.llm]   Reasoning: {reasoning}")
        print(f"[router.llm]   Tool groups: {tool_groups}")


def trace_router_llm_error(error: str) -> None:
    """Log LLM classification error."""
    if _should_log(LogLevel.ERROR):
        print(f"[router.llm] ✗ Error: {error}")


def trace_router_fallback(intent: str, reason: str) -> None:
    """Log fallback classification."""
    if _should_log(LogLevel.WARNING):
        print(f"[router] ⚠ Fallback: {intent} ({reason})")


# =============================================================================
# CONTROLLER LOGGING
# =============================================================================

def trace_run_start(question: str, run_id: str) -> None:
    """Log agent run start."""
    if _should_log(LogLevel.INFO):
        print(f"\n{'='*60}")
        print(f"[agent] Starting run: {run_id}")
        q_preview = _truncate(question.replace("\n", " "), 100)
        print(f"[agent] Goal: \"{q_preview}\"")
        print(f"{'='*60}")


def trace_step_start(step_number: int, tool_calls_count: int, facts_count: int) -> float:
    """Log step start. Returns start time."""
    if _should_log(LogLevel.INFO):
        print(f"\n[agent] ── Step {step_number} ──")
        print(f"[agent.state] Tool calls so far: {tool_calls_count}, Facts: {facts_count}")
    return perf_counter()


def trace_llm_request(tools_available: int) -> float:
    """Log LLM request. Returns start time."""
    if _should_log(LogLevel.DEBUG):
        print(f"[agent.llm] Calling LLM with {tools_available} tools available...")
    return perf_counter()


def trace_llm_response(
    duration_ms: float,
    has_tool_calls: bool,
    tool_count: int = 0,
    content_preview: Optional[str] = None,
) -> None:
    """Log LLM response."""
    if _should_log(LogLevel.INFO):
        print(f"[agent.llm] Response received ({duration_ms:.0f}ms)")
        if has_tool_calls:
            print(f"[agent.llm]   → Requested {tool_count} tool call(s)")
        elif content_preview:
            preview = _truncate(content_preview.replace("\n", " "), 100)
            print(f"[agent.llm]   → Text response: \"{preview}\"")


def trace_empty_response(step_count: int) -> None:
    """Log empty LLM response."""
    if _should_log(LogLevel.WARNING):
        print(f"[agent.llm] ⚠ Empty response at step {step_count}")


def trace_continuation_detected(content: str) -> None:
    """Log continuation intent without tool call."""
    if _should_log(LogLevel.DECISION):
        preview = _truncate(content.replace("\n", " "), 80)
        print("[agent.decision] ⚠ Continuation intent detected (no tool call)")
        print(f"[agent.decision]   Content: \"{preview}\"")
        print("[agent.decision]   → Prompting LLM to invoke tool")


def trace_malformed_output(content: str, pattern: str) -> None:
    """Log malformed tool call output."""
    if _should_log(LogLevel.ERROR):
        preview = _truncate(content.replace("\n", " "), 100)
        print(f"[agent.decision] ✗ Malformed output: {pattern}")
        print(f"[agent.decision]   Content: \"{preview}\"")
        print("[agent.decision]   → Requesting proper tool call format")


# =============================================================================
# TOOL EXECUTION LOGGING
# =============================================================================

def trace_tool_call_start(tool_name: str, args: dict[str, Any]) -> float:
    """Log tool call start. Returns start time."""
    if _should_log(LogLevel.INFO):
        args_str = _format_args(args)
        print(f"[tool.{tool_name}] Executing: {args_str}")
    return perf_counter()


def trace_pre_validation_start(tool_name: str) -> None:
    """Log pre-validation start."""
    if _should_log(LogLevel.DEBUG):
        print(f"[validation.pre] Validating {tool_name} arguments...")


def trace_pre_validation_pass(tool_name: str) -> None:
    """Log pre-validation pass."""
    if _should_log(LogLevel.DEBUG):
        print(f"[validation.pre] ✓ {tool_name} arguments valid")


def trace_pre_validation_fail(
    tool_name: str,
    errors: list[str],
    repair_attempt: int,
) -> None:
    """Log pre-validation failure."""
    if _should_log(LogLevel.WARNING):
        print(f"[validation.pre] ✗ {tool_name} validation failed")
        for error in errors[:3]:  # Limit to first 3 errors
            print(f"[validation.pre]   - {error}")
        if repair_attempt > 0:
            print(f"[validation.pre]   Repair attempt #{repair_attempt}")


def trace_tool_execution_result(
    tool_name: str,
    duration_ms: float,
    success: bool,
    result_summary: str,
) -> None:
    """Log tool execution result."""
    if _should_log(LogLevel.INFO):
        status = "✓" if success else "✗"
        print(f"[tool.{tool_name}] {status} Completed ({duration_ms:.0f}ms): {result_summary}")


def trace_tool_error(tool_name: str, error: str) -> None:
    """Log tool execution error."""
    if _should_log(LogLevel.ERROR):
        print(f"[tool.{tool_name}] ✗ Error: {_truncate(error, 200)}")


def trace_post_validation_start(tool_name: str) -> None:
    """Log post-validation start."""
    if _should_log(LogLevel.DEBUG):
        print(f"[validation.post] Validating {tool_name} result...")


def trace_post_validation_result(
    tool_name: str,
    coverage: str,
    passed: bool,
    reason: Optional[str] = None,
    suggested_tools: Optional[list[str]] = None,
) -> None:
    """Log post-validation result."""
    if _should_log(LogLevel.DECISION):
        status = "✓" if passed else "✗"
        print(f"[validation.post] {status} {tool_name}: coverage={coverage}")
        if reason:
            print(f"[validation.post]   Reason: {reason}")
        if suggested_tools:
            print(f"[validation.post]   Suggested: {suggested_tools}")


def trace_fact_extracted(fact: str) -> None:
    """Log fact extraction."""
    if _should_log(LogLevel.INFO):
        print(f"[agent.state] + Fact: {_truncate(fact, 100)}")


def trace_guidance_injected(guidance_preview: str) -> None:
    """Log guidance injection for failed tool."""
    if _should_log(LogLevel.DECISION):
        print(f"[agent.decision] → Injecting recovery guidance: {_truncate(guidance_preview, 100)}")


def trace_decision(decision: str, reason: str, details: Optional[dict[str, Any]] = None) -> None:
    """Log a controller decision."""
    if _should_log(LogLevel.DECISION):
        print(f"[agent.decision] {decision}")
        print(f"[agent.decision]   Reason: {reason}")
        if details:
            for key, value in details.items():
                if isinstance(value, list) and value:
                    print(f"[agent.decision]   {key}: {', '.join(str(v) for v in value[:3])}")
                elif value:
                    print(f"[agent.decision]   {key}: {value}")


def trace_goal_check(achieved: bool, reason: str, pending: list[str]) -> None:
    """Log goal completion check."""
    if _should_log(LogLevel.DECISION):
        status = "✓ ACHIEVED" if achieved else "⋯ IN PROGRESS"
        print(f"[agent.goal] {status}")
        print(f"[agent.goal]   {reason}")
        if pending:
            print(f"[agent.goal]   Pending: {', '.join(pending[:3])}")


# =============================================================================
# LIMIT/STOP LOGGING
# =============================================================================

def trace_limit_check(
    step_count: int,
    max_steps: int,
    tool_calls: int,
    max_tool_calls: int,
    repairs: int,
    max_repairs: int,
) -> None:
    """Log limit check status."""
    if _should_log(LogLevel.DEBUG):
        print(f"[agent.limits] Steps: {step_count}/{max_steps}, Tools: {tool_calls}/{max_tool_calls}, Repairs: {repairs}/{max_repairs}")


def trace_limit_violation(
    limit_type: str,
    message: str,
    details: dict[str, Any],
) -> None:
    """Log limit violation."""
    if _should_log(LogLevel.WARNING):
        print(f"[agent.limits] ⚠ LIMIT HIT: {limit_type}")
        print(f"[agent.limits]   {message}")
        for key, value in details.items():
            print(f"[agent.limits]   {key}: {value}")


def trace_no_progress_detected(reason: str) -> None:
    """Log no-progress detection."""
    if _should_log(LogLevel.WARNING):
        print(f"[agent.limits] ⚠ No progress detected: {reason}")


# =============================================================================
# RUN COMPLETION LOGGING
# =============================================================================

def trace_run_complete(
    run_id: str,
    success: bool,
    duration_ms: float,
    steps: int,
    tool_calls: int,
    answer_preview: Optional[str] = None,
) -> None:
    """Log run completion."""
    if _should_log(LogLevel.INFO):
        status = "✓ Success" if success else "✗ Failed"
        print(f"\n{'='*60}")
        print(f"[agent] {status} - Run {run_id}")
        print(f"[agent]   Duration: {duration_ms:.0f}ms")
        print(f"[agent]   Steps: {steps}, Tool calls: {tool_calls}")
        if answer_preview:
            preview = _truncate(answer_preview.replace("\n", " "), 100)
            print(f"[agent]   Answer: \"{preview}\"")
        print(f"{'='*60}\n")


def trace_run_error(run_id: str, error: str) -> None:
    """Log run error."""
    if _should_log(LogLevel.ERROR):
        print(f"\n{'='*60}")
        print(f"[agent] ✗ Error in run {run_id}")
        print(f"[agent]   {error}")
        print(f"{'='*60}\n")


# =============================================================================
# TOOL LIFECYCLE EVENTS (clawdbot-inspired)
# =============================================================================

def trace_tool_lifecycle_start(tool_name: str, call_id: str, args: dict[str, Any]) -> None:
    """Log tool execution lifecycle start."""
    if _should_log(LogLevel.INFO):
        args_str = _format_args(args)
        print(f"[lifecycle.tool] START: {tool_name} (id={call_id[:8]})")
        print(f"[lifecycle.tool]   args: {args_str}")


def trace_tool_lifecycle_end(
    tool_name: str,
    call_id: str,
    success: bool,
    duration_ms: float,
    result_summary: str,
) -> None:
    """Log tool execution lifecycle end."""
    if _should_log(LogLevel.INFO):
        status = "SUCCESS" if success else "FAILED"
        print(f"[lifecycle.tool] END: {tool_name} (id={call_id[:8]}) {status}")
        print(f"[lifecycle.tool]   duration: {duration_ms:.0f}ms")
        print(f"[lifecycle.tool]   result: {result_summary}")


def trace_run_lifecycle_checkpoint(
    run_id: str,
    phase: str,
    steps: int,
    tool_calls: int,
    goal_achieved: bool,
) -> None:
    """Log run lifecycle checkpoint."""
    if _should_log(LogLevel.INFO):
        goal_status = "✓" if goal_achieved else "⋯"
        print(f"[lifecycle.run] CHECKPOINT: {phase}")
        print(f"[lifecycle.run]   run={run_id[:8]} steps={steps} tools={tool_calls} goal={goal_status}")


# =============================================================================
# HOME ASSISTANT SPECIFIC LOGGING
# =============================================================================

def trace_ha_list_tools(tool_count: int, duration_ms: float) -> None:
    """Log Home Assistant list_tools result."""
    if _should_log(LogLevel.INFO):
        print(f"[tool.home_assistant] Listed {tool_count} HA tools ({duration_ms:.0f}ms)")


def trace_ha_call_tool(
    ha_tool: str,
    args: dict[str, Any],
    success: bool,
    duration_ms: float,
) -> None:
    """Log Home Assistant call_tool result."""
    if _should_log(LogLevel.INFO):
        status = "✓" if success else "✗"
        args_str = _format_args(args)
        print(f"[tool.home_assistant] {status} {ha_tool}({args_str}) ({duration_ms:.0f}ms)")


def trace_ha_mistake_detected(mistake: str, suggestion: str) -> None:
    """Log Home Assistant common mistake detection."""
    if _should_log(LogLevel.WARNING):
        print(f"[tool.home_assistant] ⚠ Mistake detected: {mistake}")
        print(f"[tool.home_assistant]   → {suggestion}")
