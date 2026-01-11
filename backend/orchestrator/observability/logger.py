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
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


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
    skill_hints: Optional[list[str]] = None

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
            "skill_hints": self.skill_hints,
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
        verbose: bool = True,
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

    def _log(self, message: str, level: str = "INFO") -> None:
        """Print a log message if verbose mode is enabled."""
        if self._verbose:
            prefix = {
                "INFO": "[agent]",
                "DECISION": "[agent.decision]",
                "TOOL": "[agent.tool]",
                "VALIDATION": "[agent.validation]",
                "STATE": "[agent.state]",
                "ERROR": "[agent.ERROR]",
            }.get(level, "[agent]")
            print(f"{prefix} {message}")

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

        print(f"[agent.logger] Started run {run_id}")
        return run_id

    def log_intent(
        self,
        run_id: str,
        intent: str,
        allowed_tool_groups: list[str],
        skill_hints: Optional[list[str]] = None,
    ) -> None:
        """Log intent routing results."""
        with self._lock:
            if run_id in self._logs:
                log = self._logs[run_id]
                log.intent = intent
                log.allowed_tool_groups = allowed_tool_groups
                log.skill_hints = skill_hints

        self._log(f"Intent classified: {intent}", "DECISION")
        self._log(f"  Allowed tool groups: {allowed_tool_groups}", "DECISION")
        if skill_hints:
            self._log(f"  Skill hints: {skill_hints}", "DECISION")

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
        args_str = json.dumps(arguments, default=str)[:100]
        self._log(f"Tool: {tool_name}({args_str})", "TOOL")

        if not pre_validation_passed:
            self._log(f"  PRE-VALIDATION FAILED: {validation_errors}", "VALIDATION")
            if repair_attempt > 0:
                self._log(f"  Repair attempt #{repair_attempt}", "VALIDATION")
        elif duration_ms > 0:
            self._log(f"  Executed in {duration_ms:.0f}ms", "TOOL")

            # Log result summary
            if result:
                if result.get("error"):
                    self._log(f"  FAILED: {result.get('error')}", "ERROR")
                elif result.get("success") is False:
                    self._log(f"  FAILED: {result.get('error', 'success=False')}", "ERROR")
                else:
                    # Success - summarize result
                    if "count" in result:
                        self._log(f"  Result: {result['count']} items returned", "TOOL")
                    elif "tools" in result:
                        self._log(f"  Result: Listed {len(result['tools'])} tools", "TOOL")
                    elif "rows" in result:
                        self._log(f"  Result: {len(result['rows'])} rows", "TOOL")
                    elif "results" in result:
                        self._log(f"  Result: {len(result['results'])} results", "TOOL")
                    else:
                        self._log("  Result: OK", "TOOL")

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

            print(
                f"[agent.logger] Completed run {run_id}: "
                f"success={success}, steps={log.total_steps}, "
                f"tool_calls={log.total_tool_calls}, duration={log.duration_ms:.1f}ms"
            )

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
                "avg_steps": sum(log.total_steps for log in completed) / len(completed) if completed else 0,
                "avg_tool_calls": sum(log.total_tool_calls for log in completed) / len(completed) if completed else 0,
                "avg_duration_ms": sum(log.duration_ms or 0 for log in completed) / len(completed) if completed else 0,
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
                _logger = AgentLogger()
    return _logger
