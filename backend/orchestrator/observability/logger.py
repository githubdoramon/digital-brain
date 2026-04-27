"""
Structured logging for agent runs.

Captures full request details for debugging and evaluation:
- User input and visible tools
- Model outputs and validation errors
- Executed commands and tool results
- Stop reason and timing

This data enables:
- Debugging production issues
- Evaluating agent performance
- Future fine-tuning (LoRA)
"""

import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from time import perf_counter
from typing import Any, Optional

from observability.log_stream import DECISION_LEVEL, INTENTIONAL_DEBUG_LEVEL

logger = logging.getLogger(__name__)


def get_runtime_logger(name: str) -> logging.Logger:
    """Return a module logger wired into orchestrator observability."""
    return logging.getLogger(name)


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
    min_level: LogLevel = LogLevel.DEBUG
    show_timestamps: bool = False
    truncate_length: int = 150

    @classmethod
    def from_env(cls) -> "TraceConfig":
        return cls(
            enabled=os.getenv("AGENT_TRACE_ENABLED", "true").lower() == "true",
            min_level=LogLevel(os.getenv("AGENT_TRACE_LEVEL", "debug").lower()),
            show_timestamps=os.getenv("AGENT_TRACE_TIMESTAMPS", "false").lower() == "true",
            truncate_length=int(os.getenv("AGENT_TRACE_TRUNCATE", "150")),
        )


_config: Optional[TraceConfig] = None
_contact_resolution_counters: dict[str, int] = {
    "resolved": 0,
    "ambiguous": 0,
    "auto_disambiguated": 0,
    "clarification_returned": 0,
}


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

    level_order = [
        LogLevel.DEBUG,
        LogLevel.INFO,
        LogLevel.DECISION,
        LogLevel.WARNING,
        LogLevel.ERROR,
    ]
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
        return json.dumps(args, default=str)
    except Exception:
        return str(args)


def _emit(level: LogLevel, message: str) -> None:
    if level == LogLevel.DEBUG:
        logger.log(INTENTIONAL_DEBUG_LEVEL, message)
    elif level == LogLevel.INFO:
        logger.info(message)
    elif level == LogLevel.DECISION:
        logger.log(DECISION_LEVEL, message)
    elif level == LogLevel.WARNING:
        logger.warning(message)
    else:
        logger.error(message)


@dataclass
class ToolCallLog:
    """Log of a single tool call."""

    tool_name: str
    arguments: dict[str, Any]

    # Validation
    pre_validation_passed: bool = True
    validation_errors: Optional[list[str]] = None
    repair_attempt: int = 0

    # Execution
    duration_ms: float = 0
    result: Optional[dict[str, Any]] = None

    # Post-validation
    goal_coverage: Optional[str] = None
    extracted_facts: Optional[list[str]] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StepLog:
    """Log of a single agent step (one LLM call iteration)."""

    step_number: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # LLM call metrics
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    llm_duration_ms: Optional[float] = None

    # Model output
    model_content: Optional[str] = None
    model_reasoning: Optional[str] = None
    had_tool_calls: bool = False

    # Tool calls in this step
    tool_calls: list[ToolCallLog] = field(default_factory=list)

    # State snapshot (optional, for debugging)
    state_snapshot: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "step_number": self.step_number,
            "timestamp": self.timestamp.isoformat(),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "llm_duration_ms": self.llm_duration_ms,
            "model_content": self.model_content[:500] if self.model_content else None,
            "model_reasoning": self.model_reasoning[:500] if self.model_reasoning else None,
            "had_tool_calls": self.had_tool_calls,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
        }
        if self.state_snapshot:
            result["state_snapshot"] = self.state_snapshot
        return result


@dataclass
class AgentRunLog:
    """Complete log of an agent run for debugging and evaluation."""

    run_id: str
    question: str
    user_id: str
    session_id: Optional[str] = None

    # Timing
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    duration_ms: Optional[float] = None

    # Intent routing
    intent: Optional[str] = None
    allowed_tool_groups: Optional[list[str]] = None
    # Execution trace
    steps: list[StepLog] = field(default_factory=list)

    # Summary stats
    total_steps: int = 0
    total_tool_calls: int = 0
    total_repairs: int = 0

    # Outcome
    success: bool = True
    final_answer: Optional[str] = None
    error: Optional[str] = None
    limit_hit: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "question": self.question,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": self.duration_ms,
            "intent": self.intent,
            "allowed_tool_groups": self.allowed_tool_groups,
            "steps": [s.to_dict() for s in self.steps],
            "total_steps": self.total_steps,
            "total_tool_calls": self.total_tool_calls,
            "total_repairs": self.total_repairs,
            "success": self.success,
            "final_answer": self.final_answer[:500] if self.final_answer else None,
            "error": self.error,
            "limit_hit": self.limit_hit,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


class AgentLogger:
    """
    Structured logger for agent runs.

    Captures full request details for debugging and evaluation.
    Thread-safe for concurrent requests.
    """

    def __init__(
        self,
        max_logs: int = 1000,
        enable_state_snapshots: bool = False,
        verbose: bool = False,
    ):
        """
        Initialize the logger.

        Args:
            max_logs: Maximum number of logs to keep in memory
            enable_state_snapshots: Whether to capture state snapshots at each step
            verbose: Whether to print detailed logs to console
        """
        self._logs: dict[str, AgentRunLog] = {}
        self._lock = threading.Lock()
        self._max_logs = max_logs
        self._enable_state_snapshots = enable_state_snapshots
        self._verbose = verbose

    def _log(self, _message: str, _level: str = "INFO") -> None:
        """No-op runtime emission; tracer helpers own live logging output."""
        return

    def start_run(
        self,
        question: str,
        user_id: str,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> str:
        """
        Start logging a new agent run.

        Args:
            question: The user's question
            user_id: User identifier
            session_id: Optional session/thread ID
            run_id: Optional custom run ID (generated if not provided)

        Returns:
            The run ID
        """
        run_id = run_id or str(uuid.uuid4())

        log = AgentRunLog(
            run_id=run_id,
            question=question,
            user_id=user_id,
            session_id=session_id,
        )

        with self._lock:
            # Evict old logs if at capacity
            if len(self._logs) >= self._max_logs:
                oldest = min(self._logs.values(), key=lambda x: x.started_at)
                del self._logs[oldest.run_id]

            self._logs[run_id] = log

        return run_id

    def log_intent(
        self,
        run_id: str,
        intent: str,
        allowed_tool_groups: list[str],
    ) -> None:
        """Log intent routing results."""
        with self._lock:
            if run_id in self._logs:
                log = self._logs[run_id]
                log.intent = intent
                log.allowed_tool_groups = allowed_tool_groups

        self._log(f"Intent classified: {intent}", "DECISION")
        self._log(f"  Allowed tool groups: {allowed_tool_groups}", "DECISION")

    def start_step(
        self,
        run_id: str,
        step_number: int,
        state_snapshot: Optional[dict[str, Any]] = None,
    ) -> StepLog:
        """
        Start logging a new step.

        Args:
            run_id: The run ID
            step_number: Current step number
            state_snapshot: Optional state snapshot for debugging

        Returns:
            StepLog object to populate
        """
        step = StepLog(
            step_number=step_number,
            state_snapshot=state_snapshot if self._enable_state_snapshots else None,
        )

        with self._lock:
            if run_id in self._logs:
                self._logs[run_id].steps.append(step)
                self._logs[run_id].total_steps = step_number

        self._log(f"--- Step {step_number} ---", "INFO")

        return step

    def log_llm_call(
        self,
        run_id: str,
        step_number: int,
        duration_ms: float,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        content: Optional[str] = None,
        reasoning: Optional[str] = None,
        had_tool_calls: bool = False,
    ) -> None:
        """Log LLM call metrics for a step."""
        with self._lock:
            if run_id not in self._logs:
                return

            log = self._logs[run_id]
            for step in log.steps:
                if step.step_number == step_number:
                    step.llm_duration_ms = duration_ms
                    step.prompt_tokens = prompt_tokens
                    step.completion_tokens = completion_tokens
                    step.model_content = content
                    step.model_reasoning = reasoning
                    step.had_tool_calls = had_tool_calls
                    break

        self._log(f"LLM response ({duration_ms:.0f}ms)", "INFO")
        if had_tool_calls:
            self._log("  LLM requested tool calls", "DECISION")
        elif content:
            preview = content[:100].replace("\n", " ")
            self._log(f"  LLM text response: {preview}...", "INFO")

    def log_tool_call(
        self,
        run_id: str,
        step_number: int,
        tool_name: str,
        arguments: dict[str, Any],
        pre_validation_passed: bool = True,
        validation_errors: Optional[list[str]] = None,
        repair_attempt: int = 0,
        duration_ms: float = 0,
        result: Optional[dict[str, Any]] = None,
        goal_coverage: Optional[str] = None,
        extracted_facts: Optional[list[str]] = None,
    ) -> None:
        """Log a tool call within a step."""
        tool_log = ToolCallLog(
            tool_name=tool_name,
            arguments=arguments,
            pre_validation_passed=pre_validation_passed,
            validation_errors=validation_errors,
            repair_attempt=repair_attempt,
            duration_ms=duration_ms,
            result=result,
            goal_coverage=goal_coverage,
            extracted_facts=extracted_facts,
        )

        with self._lock:
            if run_id not in self._logs:
                return

            log = self._logs[run_id]
            log.total_tool_calls += 1

            if repair_attempt > 0:
                log.total_repairs += 1

            for step in log.steps:
                if step.step_number == step_number:
                    step.tool_calls.append(tool_log)
                    step.had_tool_calls = True
                    break

        # Log tool call details
        args_str = json.dumps(arguments, default=str)
        self._log(f"Tool: {tool_name}({args_str})", "DEBUG")

        if not pre_validation_passed:
            self._log(f"  PRE-VALIDATION FAILED: {validation_errors}", "VALIDATION")
            if repair_attempt > 0:
                self._log(f"  Repair attempt #{repair_attempt}", "VALIDATION")
        elif duration_ms > 0:
            self._log(f"  Executed in {duration_ms:.0f}ms", "DEBUG")

            # Log result summary
            if result:
                if result.get("error"):
                    self._log(f"  FAILED: {result.get('error')}", "ERROR")
                elif result.get("success") is False:
                    self._log(f"  FAILED: {result.get('error', 'success=False')}", "ERROR")
                else:
                    # Success - summarize result
                    if "count" in result:
                        self._log(f"  Result: {result['count']} items returned", "DEBUG")
                    elif "tools" in result:
                        self._log(f"  Result: Listed {len(result['tools'])} tools", "DEBUG")
                    elif "rows" in result:
                        self._log(f"  Result: {len(result['rows'])} rows", "DEBUG")
                    elif "results" in result:
                        self._log(f"  Result: {len(result['results'])} results", "DEBUG")
                    else:
                        self._log("  Result: OK", "DEBUG")

                    preview = self._compact_result_preview(result)
                    if preview:
                        self._log(f"  Preview: {preview}", "DEBUG")

        # Log post-validation
        if goal_coverage:
            self._log(f"  Goal coverage: {goal_coverage}", "VALIDATION")
        if extracted_facts:
            for fact in extracted_facts[:3]:  # Limit to first 3
                self._log(f"  + Fact: {fact}", "STATE")

    def log_decision(
        self,
        decision: str,
        reason: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """Log an agent decision point."""
        self._log(f"DECISION: {decision}", "DECISION")
        self._log(f"  Reason: {reason}", "DECISION")
        if details:
            for key, value in details.items():
                self._log(f"  {key}: {value}", "DECISION")

    def _compact_result_preview(self, result: dict[str, Any]) -> str:
        """Build a compact, debug-safe preview for retrieval-style tool results."""
        if not isinstance(result, dict):
            return ""

        if isinstance(result.get("document"), dict):
            doc = result["document"]
            document_id = str(doc.get("document_id") or "").strip()
            title = str(doc.get("title") or "").strip()
            bits = []
            if document_id:
                bits.append(document_id)
            if title:
                bits.append(title)
            return "document=" + " | ".join(bits) if bits else ""

        if isinstance(result.get("events"), list):
            events = [item for item in result.get("events", []) if isinstance(item, dict)]
            top = events[:3]
            if not top:
                return ""
            formatted = []
            for event in top:
                event_id = str(event.get("id") or event.get("event_id") or "").strip()
                title = str(event.get("title") or "").strip()
                date = str(event.get("start_date") or "").strip()
                parts = [part for part in (event_id, title, date) if part]
                if parts:
                    formatted.append(" | ".join(parts))
            return "; ".join(formatted)

        if isinstance(result.get("results"), list):
            rows = [item for item in result.get("results", []) if isinstance(item, dict)]
            top = rows[:3]
            if not top:
                return ""
            formatted = []
            for row in top:
                row_id = str(row.get("id") or "").strip()
                kind = str(row.get("kind") or "").strip()
                title = str(row.get("title") or "").strip()
                score = row.get("score")
                score_text = ""
                try:
                    score_text = f" score={float(score):.3f}" if score is not None else ""
                except (TypeError, ValueError):
                    score_text = ""
                parts = [part for part in (kind, title, row_id) if part]
                if parts:
                    formatted.append(" | ".join(parts) + score_text)
            return "; ".join(formatted)

        return ""

    def log_validation_result(
        self,
        validation_type: str,
        passed: bool,
        coverage: Optional[str] = None,
        reason: Optional[str] = None,
        suggested_tools: Optional[list[str]] = None,
    ) -> None:
        """Log a validation result."""
        status = "PASSED" if passed else "FAILED"
        self._log(f"{validation_type} validation: {status}", "VALIDATION")
        if coverage:
            self._log(f"  Coverage: {coverage}", "VALIDATION")
        if reason:
            self._log(f"  Reason: {reason}", "VALIDATION")
        if suggested_tools:
            self._log(f"  Suggested tools: {suggested_tools}", "VALIDATION")

    def log_state_update(
        self,
        update_type: str,
        value: str,
    ) -> None:
        """Log a state update."""
        self._log(f"{update_type}: {value}", "STATE")

    def log_malformed_output(
        self,
        content_preview: str,
        detected_pattern: str,
    ) -> None:
        """Log detection of malformed LLM output."""
        self._log(f"MALFORMED OUTPUT DETECTED: {detected_pattern}", "ERROR")
        self._log(f"  Content preview: {content_preview[:100]}...", "ERROR")
        self._log("  Requesting LLM to retry with proper tool call format", "DECISION")

    def log_continuation_detected(
        self,
        content_preview: str,
    ) -> None:
        """Log detection of continuation intent without tool call."""
        self._log("CONTINUATION INTENT WITHOUT TOOL CALL", "DECISION")
        self._log(f"  Content: {content_preview[:80]}...", "DECISION")
        self._log("  Prompting LLM to actually invoke the tool", "DECISION")

    def complete_run(
        self,
        run_id: str,
        success: bool = True,
        final_answer: Optional[str] = None,
        error: Optional[str] = None,
        limit_hit: Optional[str] = None,
    ) -> Optional[AgentRunLog]:
        """
        Complete an agent run log.

        Args:
            run_id: The run ID
            success: Whether the run succeeded
            final_answer: The final answer (if any)
            error: Error message (if failed)
            limit_hit: Limit that was hit (if any)

        Returns:
            The completed AgentRunLog, or None if not found
        """
        with self._lock:
            if run_id not in self._logs:
                return None

            log = self._logs[run_id]
            log.completed_at = datetime.now(timezone.utc)
            log.duration_ms = (log.completed_at - log.started_at).total_seconds() * 1000
            log.success = success
            log.final_answer = final_answer
            log.error = error
            log.limit_hit = limit_hit

            return log

    def get_log(self, run_id: str) -> Optional[AgentRunLog]:
        """Get a run log by ID."""
        with self._lock:
            return self._logs.get(run_id)

    def export_json(self, run_id: str) -> Optional[str]:
        """Export a run log as JSON."""
        log = self.get_log(run_id)
        return log.to_json() if log else None

    def get_recent_logs(self, n: int = 10) -> list[AgentRunLog]:
        """Get the N most recent run logs."""
        with self._lock:
            sorted_logs = sorted(
                self._logs.values(),
                key=lambda x: x.started_at,
                reverse=True,
            )
            return sorted_logs[:n]

    def get_stats(self) -> dict[str, Any]:
        """Get summary statistics across all logged runs."""
        with self._lock:
            if not self._logs:
                return {"total_runs": 0}

            logs = list(self._logs.values())
            completed = [log for log in logs if log.completed_at]
            successful = [log for log in completed if log.success]

            return {
                "total_runs": len(logs),
                "completed_runs": len(completed),
                "successful_runs": len(successful),
                "success_rate": len(successful) / len(completed) if completed else 0,
                "avg_steps": sum(log.total_steps for log in completed) / len(completed)
                if completed
                else 0,
                "avg_tool_calls": sum(log.total_tool_calls for log in completed) / len(completed)
                if completed
                else 0,
                "avg_duration_ms": sum(log.duration_ms or 0 for log in completed) / len(completed)
                if completed
                else 0,
            }


# Singleton instance
_logger: Optional[AgentLogger] = None
_logger_lock = threading.Lock()


def get_logger() -> AgentLogger:
    """Get the singleton AgentLogger instance."""
    global _logger
    if _logger is None:
        with _logger_lock:
            if _logger is None:
                _logger = AgentLogger(verbose=False)
    return _logger


# =============================================================================
# TRACE HELPERS
# =============================================================================


def trace_router_start(question: str) -> float:
    """Log router classification start. Returns start time."""
    if _should_log(LogLevel.INFO):
        q_preview = _truncate(question.replace("\n", " "), 80)
        _emit(LogLevel.INFO, f'[router] Classifying intent: "{q_preview}"')
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
        _emit(LogLevel.DECISION, f"[router.rule] ✓ Matched: {intent} (confidence={confidence:.2f})")
        _emit(LogLevel.DECISION, f"[router.rule]   Reasoning: {reasoning}")
        _emit(LogLevel.DECISION, f"[router.rule]   Tool groups: {tool_groups}")
        _emit(LogLevel.DECISION, f"[router.rule]   Duration: {duration_ms:.1f}ms")


def trace_router_llm_start() -> float:
    """Log LLM classification start. Returns start time."""
    if _should_log(LogLevel.INFO):
        _emit(LogLevel.INFO, "[router.llm] Starting LLM routing...")
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
        _emit(
            LogLevel.DECISION,
            f"[router.llm] ✓ Classified: {intent} (confidence={confidence:.2f}, {duration_ms:.0f}ms)",
        )
        if reasoning:
            _emit(LogLevel.DECISION, f"[router.llm]   Reasoning: {reasoning}")
        _emit(LogLevel.DECISION, f"[router.llm]   Tool groups: {tool_groups}")


def trace_router_llm_error(error: str) -> None:
    """Log LLM classification error."""
    if _should_log(LogLevel.ERROR):
        _emit(LogLevel.ERROR, f"[router.llm] ✗ Error: {error}")


def trace_router_fallback(intent: str, reason: str) -> None:
    """Log fallback classification."""
    if _should_log(LogLevel.WARNING):
        _emit(LogLevel.WARNING, f"[router] ⚠ Fallback: {intent} ({reason})")


def trace_run_start(question: str, run_id: str) -> None:
    """Log agent run start."""
    if _should_log(LogLevel.INFO):
        _emit(LogLevel.INFO, f"\n{'=' * 60}")
        _emit(LogLevel.INFO, f"[agent] Starting run: {run_id}")
        q_preview = _truncate(question.replace("\n", " "), 100)
        _emit(LogLevel.INFO, f'[agent] Goal: "{q_preview}"')
        _emit(LogLevel.INFO, f"{'=' * 60}")


def trace_step_start(step_number: int, tool_calls_count: int, facts_count: int) -> float:
    """Log step start. Returns start time."""
    if _should_log(LogLevel.INFO):
        _emit(LogLevel.INFO, f"\n[agent] ── Step {step_number} ──")
        _emit(
            LogLevel.INFO,
            f"[agent.state] Tool calls so far: {tool_calls_count}, Facts: {facts_count}",
        )
    return perf_counter()


def trace_llm_request(tools_available: int) -> float:
    """Log LLM request. Returns start time."""
    if _should_log(LogLevel.DEBUG):
        _emit(LogLevel.DEBUG, f"[agent.llm] Calling LLM with {tools_available} tools available...")
    return perf_counter()


def trace_llm_response(
    duration_ms: float,
    has_tool_calls: bool,
    tool_count: int = 0,
    content_preview: Optional[str] = None,
    reasoning_preview: Optional[str] = None,
) -> None:
    """Log LLM response."""
    if _should_log(LogLevel.INFO):
        _emit(LogLevel.INFO, f"[agent.llm] Response received ({duration_ms:.0f}ms)")
        if has_tool_calls:
            _emit(LogLevel.INFO, f"[agent.llm]   → Requested {tool_count} tool call(s)")
        if content_preview:
            preview = _truncate(content_preview.replace("\n", " "), 100)
            _emit(LogLevel.INFO, f'[agent.llm]   → Text response: "{preview}"')
        if reasoning_preview:
            preview = _truncate(reasoning_preview.replace("\n", " "), 160)
            _emit(LogLevel.INFO, f'[agent.llm]   → Reasoning: "{preview}"')


def trace_empty_response(step_count: int) -> None:
    """Log empty LLM response."""
    if _should_log(LogLevel.WARNING):
        _emit(LogLevel.WARNING, f"[agent.llm] ⚠ Empty response at step {step_count}")


def trace_continuation_detected(content: str) -> None:
    """Log continuation intent without tool call."""
    if _should_log(LogLevel.DECISION):
        preview = _truncate(content.replace("\n", " "), 80)
        _emit(LogLevel.DECISION, "[agent.decision] ⚠ Continuation intent detected (no tool call)")
        _emit(LogLevel.DECISION, f'[agent.decision]   Content: "{preview}"')
        _emit(LogLevel.DECISION, "[agent.decision]   → Prompting LLM to invoke tool")


def trace_malformed_output(content: str, pattern: str) -> None:
    """Log malformed tool call output."""
    if _should_log(LogLevel.ERROR):
        preview = _truncate(content.replace("\n", " "), 100)
        _emit(LogLevel.ERROR, f"[agent.decision] ✗ Malformed output: {pattern}")
        _emit(LogLevel.ERROR, f'[agent.decision]   Content: "{preview}"')
        _emit(LogLevel.ERROR, "[agent.decision]   → Requesting proper tool call format")


def trace_tool_call_start(tool_name: str, args: dict[str, Any]) -> float:
    """Log tool call start. Returns start time."""
    if _should_log(LogLevel.DEBUG):
        args_str = _format_args(args)
        _emit(LogLevel.DEBUG, f"[tool.{tool_name}] Executing: {args_str}")
    return perf_counter()


def trace_tool_args_normalized(tool_name: str, args: dict[str, Any]) -> None:
    """Log normalized tool arguments after controller enrichment."""
    if _should_log(LogLevel.DEBUG):
        args_str = _format_args(args)
        _emit(LogLevel.DEBUG, f"[tool.{tool_name}] Normalized args: {args_str}")


def trace_pre_validation_start(tool_name: str) -> None:
    """Log pre-validation start."""
    if _should_log(LogLevel.DEBUG):
        _emit(LogLevel.DEBUG, f"[validation.pre] Validating {tool_name} arguments...")


def trace_pre_validation_pass(tool_name: str) -> None:
    """Log pre-validation pass."""
    if _should_log(LogLevel.DEBUG):
        _emit(LogLevel.DEBUG, f"[validation.pre] ✓ {tool_name} arguments valid")


def trace_pre_validation_fail(
    tool_name: str,
    errors: list[str],
    repair_attempt: int,
) -> None:
    """Log pre-validation failure."""
    if _should_log(LogLevel.WARNING):
        _emit(LogLevel.WARNING, f"[validation.pre] ✗ {tool_name} validation failed")
        for error in errors[:3]:
            _emit(LogLevel.WARNING, f"[validation.pre]   - {error}")
        if repair_attempt > 0:
            _emit(LogLevel.WARNING, f"[validation.pre]   Repair attempt #{repair_attempt}")


def trace_tool_execution_result(
    tool_name: str,
    duration_ms: float,
    success: bool,
    result_summary: str,
) -> None:
    """Log tool execution result."""
    if _should_log(LogLevel.DEBUG):
        status = "✓" if success else "✗"
        _emit(
            LogLevel.DEBUG,
            f"[tool.{tool_name}] {status} Completed ({duration_ms:.0f}ms): {result_summary}",
        )


def trace_tool_error(tool_name: str, error: str) -> None:
    """Log tool execution error."""
    if _should_log(LogLevel.ERROR):
        _emit(LogLevel.ERROR, f"[tool.{tool_name}] ✗ Error: {_truncate(error, 200)}")


def trace_post_validation_start(tool_name: str) -> None:
    """Log post-validation start."""
    if _should_log(LogLevel.DEBUG):
        _emit(LogLevel.DEBUG, f"[validation.post] Validating {tool_name} result...")


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
        _emit(LogLevel.DECISION, f"[validation.post] {status} {tool_name}: coverage={coverage}")
        if reason:
            _emit(LogLevel.DECISION, f"[validation.post]   Reason: {reason}")
        if suggested_tools:
            _emit(LogLevel.DECISION, f"[validation.post]   Suggested: {suggested_tools}")


def trace_fact_extracted(fact: str) -> None:
    """Log fact extraction."""
    if _should_log(LogLevel.INFO):
        _emit(LogLevel.INFO, f"[agent.state] + Fact: {_truncate(fact, 100)}")


def trace_guidance_injected(guidance_preview: str) -> None:
    """Log guidance injection for failed tool."""
    if _should_log(LogLevel.DECISION):
        _emit(
            LogLevel.DECISION,
            f"[agent.decision] → Injecting recovery guidance: {_truncate(guidance_preview, 100)}",
        )


def trace_decision(decision: str, reason: str, details: Optional[dict[str, Any]] = None) -> None:
    """Log a controller decision."""
    if _should_log(LogLevel.DECISION):
        _emit(LogLevel.DECISION, f"[agent.decision] {decision}")
        _emit(LogLevel.DECISION, f"[agent.decision]   Reason: {reason}")
        if details:
            for key, value in details.items():
                if isinstance(value, list) and value:
                    _emit(
                        LogLevel.DECISION,
                        f"[agent.decision]   {key}: {', '.join(str(v) for v in value[:3])}",
                    )
                elif value:
                    _emit(LogLevel.DECISION, f"[agent.decision]   {key}: {value}")


def trace_goal_check(achieved: bool, reason: str, pending: list[str]) -> None:
    """Log goal completion check."""
    if _should_log(LogLevel.DECISION):
        status = "✓ ACHIEVED" if achieved else "⋯ IN PROGRESS"
        _emit(LogLevel.DECISION, f"[agent.goal] {status}")
        _emit(LogLevel.DECISION, f"[agent.goal]   {reason}")
        if pending:
            _emit(LogLevel.DECISION, f"[agent.goal]   Pending: {', '.join(pending[:3])}")


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
        _emit(
            LogLevel.DEBUG,
            f"[agent.limits] Steps: {step_count}/{max_steps}, Tools: {tool_calls}/{max_tool_calls}, Repairs: {repairs}/{max_repairs}",
        )


def trace_limit_violation(
    limit_type: str,
    message: str,
    details: dict[str, Any],
) -> None:
    """Log limit violation."""
    if _should_log(LogLevel.WARNING):
        _emit(LogLevel.WARNING, f"[agent.limits] ⚠ LIMIT HIT: {limit_type}")
        _emit(LogLevel.WARNING, f"[agent.limits]   {message}")
        for key, value in details.items():
            _emit(LogLevel.WARNING, f"[agent.limits]   {key}: {value}")


def trace_no_progress_detected(reason: str) -> None:
    """Log no-progress detection."""
    if _should_log(LogLevel.WARNING):
        _emit(LogLevel.WARNING, f"[agent.limits] ⚠ No progress detected: {reason}")


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
        _emit(LogLevel.INFO, f"\n{'=' * 60}")
        _emit(LogLevel.INFO, f"[agent] {status} - Run {run_id}")
        _emit(LogLevel.INFO, f"[agent]   Duration: {duration_ms:.0f}ms")
        _emit(LogLevel.INFO, f"[agent]   Steps: {steps}, Tool calls: {tool_calls}")
        if answer_preview:
            preview = _truncate(answer_preview.replace("\n", " "), 100)
            _emit(LogLevel.INFO, f'[agent]   Answer: "{preview}"')
        _emit(LogLevel.INFO, f"{'=' * 60}\n")


def trace_run_error(run_id: str, error: str) -> None:
    """Log run error."""
    if _should_log(LogLevel.ERROR):
        _emit(LogLevel.ERROR, f"\n{'=' * 60}")
        _emit(LogLevel.ERROR, f"[agent] ✗ Error in run {run_id}")
        _emit(LogLevel.ERROR, f"[agent]   {error}")
        _emit(LogLevel.ERROR, f"{'=' * 60}\n")


def trace_contact_resolution_outcome(
    outcome: str,
    details: Optional[dict[str, Any]] = None,
) -> None:
    """Log and count contact-resolution outcomes."""
    if outcome not in _contact_resolution_counters:
        return

    _contact_resolution_counters[outcome] += 1

    if _should_log(LogLevel.INFO):
        total = _contact_resolution_counters[outcome]
        _emit(LogLevel.INFO, f"[contact_resolution] outcome={outcome} total={total}")
        if details:
            for key, value in details.items():
                if value is not None and value != "":
                    _emit(LogLevel.INFO, f"[contact_resolution]   {key}: {value}")


def trace_contact_resolution_phase(
    phase: str,
    details: Optional[dict[str, Any]] = None,
) -> None:
    """Log contact-resolution phase details without affecting counters."""
    if not _should_log(LogLevel.INFO):
        return

    _emit(LogLevel.INFO, f"[contact_resolution.phase] phase={phase}")
    if details:
        for key, value in details.items():
            if value is not None and value != "":
                _emit(LogLevel.INFO, f"[contact_resolution.phase]   {key}: {value}")


def trace_tool_lifecycle_start(tool_name: str, call_id: str, args: dict[str, Any]) -> None:
    """Log tool execution lifecycle start."""
    if _should_log(LogLevel.INFO):
        args_str = _format_args(args)
        _emit(LogLevel.INFO, f"[lifecycle.tool] START: {tool_name} (id={call_id[:8]})")
        _emit(LogLevel.INFO, f"[lifecycle.tool]   args: {args_str}")


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
        _emit(LogLevel.INFO, f"[lifecycle.tool] END: {tool_name} (id={call_id[:8]}) {status}")
        _emit(LogLevel.INFO, f"[lifecycle.tool]   duration: {duration_ms:.0f}ms")
        _emit(LogLevel.INFO, f"[lifecycle.tool]   result: {result_summary}")


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
        _emit(LogLevel.INFO, f"[lifecycle.run] CHECKPOINT: {phase}")
        _emit(
            LogLevel.INFO,
            f"[lifecycle.run]   run={run_id[:8]} steps={steps} tools={tool_calls} goal={goal_status}",
        )


def trace_ha_list_tools(tool_count: int, duration_ms: float) -> None:
    """Log Home Assistant list_tools result."""
    if _should_log(LogLevel.INFO):
        _emit(
            LogLevel.INFO,
            f"[tool.home_assistant] Listed {tool_count} HA tools ({duration_ms:.0f}ms)",
        )


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
        _emit(
            LogLevel.INFO,
            f"[tool.home_assistant] {status} {ha_tool}({args_str}) ({duration_ms:.0f}ms)",
        )


def trace_ha_mistake_detected(mistake: str, suggestion: str) -> None:
    """Log Home Assistant common mistake detection."""
    if _should_log(LogLevel.WARNING):
        _emit(LogLevel.WARNING, f"[tool.home_assistant] ⚠ Mistake detected: {mistake}")
        _emit(LogLevel.WARNING, f"[tool.home_assistant]   → {suggestion}")
