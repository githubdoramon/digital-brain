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
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import threading


@dataclass
class ToolCallLog:
    """Log of a single tool call."""

    tool_name: str
    arguments: Dict[str, Any]

    # Validation
    pre_validation_passed: bool = True
    validation_errors: Optional[List[str]] = None
    repair_attempt: int = 0

    # Execution
    duration_ms: float = 0
    result: Optional[Dict[str, Any]] = None

    # Post-validation
    goal_coverage: Optional[str] = None
    extracted_facts: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
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
    tool_calls: List[ToolCallLog] = field(default_factory=list)

    # State snapshot (optional, for debugging)
    state_snapshot: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
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
    allowed_tool_groups: Optional[List[str]] = None
    skill_hints: Optional[List[str]] = None

    # Execution trace
    steps: List[StepLog] = field(default_factory=list)

    # Summary stats
    total_steps: int = 0
    total_tool_calls: int = 0
    total_repairs: int = 0

    # Outcome
    success: bool = True
    final_answer: Optional[str] = None
    error: Optional[str] = None
    limit_hit: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
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
    ):
        """
        Initialize the logger.

        Args:
            max_logs: Maximum number of logs to keep in memory
            enable_state_snapshots: Whether to capture state snapshots at each step
        """
        self._logs: Dict[str, AgentRunLog] = {}
        self._lock = threading.Lock()
        self._max_logs = max_logs
        self._enable_state_snapshots = enable_state_snapshots

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
        allowed_tool_groups: List[str],
        skill_hints: Optional[List[str]] = None,
    ) -> None:
        """Log intent routing results."""
        with self._lock:
            if run_id in self._logs:
                log = self._logs[run_id]
                log.intent = intent
                log.allowed_tool_groups = allowed_tool_groups
                log.skill_hints = skill_hints

    def start_step(
        self,
        run_id: str,
        step_number: int,
        state_snapshot: Optional[Dict[str, Any]] = None,
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

    def log_tool_call(
        self,
        run_id: str,
        step_number: int,
        tool_name: str,
        arguments: Dict[str, Any],
        pre_validation_passed: bool = True,
        validation_errors: Optional[List[str]] = None,
        repair_attempt: int = 0,
        duration_ms: float = 0,
        result: Optional[Dict[str, Any]] = None,
        goal_coverage: Optional[str] = None,
        extracted_facts: Optional[List[str]] = None,
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

    def get_recent_logs(self, n: int = 10) -> List[AgentRunLog]:
        """Get the N most recent run logs."""
        with self._lock:
            sorted_logs = sorted(
                self._logs.values(),
                key=lambda x: x.started_at,
                reverse=True,
            )
            return sorted_logs[:n]

    def get_stats(self) -> Dict[str, Any]:
        """Get summary statistics across all logged runs."""
        with self._lock:
            if not self._logs:
                return {"total_runs": 0}

            logs = list(self._logs.values())
            completed = [l for l in logs if l.completed_at]
            successful = [l for l in completed if l.success]

            return {
                "total_runs": len(logs),
                "completed_runs": len(completed),
                "successful_runs": len(successful),
                "success_rate": len(successful) / len(completed) if completed else 0,
                "avg_steps": sum(l.total_steps for l in completed) / len(completed) if completed else 0,
                "avg_tool_calls": sum(l.total_tool_calls for l in completed) / len(completed) if completed else 0,
                "avg_duration_ms": sum(l.duration_ms or 0 for l in completed) / len(completed) if completed else 0,
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
