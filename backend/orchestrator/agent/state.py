"""
Canonical state object for the bounded agent.

This state is maintained by the controller (not the model) and
injected into every model call for consistent context.

The controller is the single source of truth for:
- Goal and constraints
- Known facts accumulated from tool results
- Completed actions
- Progress tracking (steps, tool calls, repairs)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

MAX_INFORMATION_CANDIDATES = 24
MAX_INFORMATION_CANDIDATES_IN_CONTEXT = 6
MAX_EPISODIC_MEMORIES = 24
MAX_EPISODIC_IN_CONTEXT = 6


@dataclass
class ToolCallRecord:
    """Record of a single tool call execution."""

    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    duration_ms: float
    success: bool
    error: Optional[str] = None
    validation_errors: Optional[list[str]] = None
    was_repaired: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result": self.result,
            "duration_ms": self.duration_ms,
            "success": self.success,
            "error": self.error,
            "validation_errors": self.validation_errors,
            "was_repaired": self.was_repaired,
        }


@dataclass
class AgentState:
    """
    Controller-maintained state object.

    This state is the single source of truth, maintained by the controller
    and injected into every model call. The model never modifies state directly.

    Attributes:
        goal: The user's original question/request
        constraints: Restrictions on tool usage (e.g., "read_only")
        known_facts: Facts accumulated from tool results
        completed_actions: Description of actions taken
        pending_questions: Questions to ask the user
        tool_calls: Full record of all tool executions
        step_count: Number of LLM call iterations
        repair_count: Number of validation repair attempts
        intent: Classified intent from router (metadata only)
        allowed_tool_groups: Router-provided groups

        # Completion tracking (clawdbot-inspired)
        goal_achieved: Whether the user's goal was actually accomplished
        pending_actions: Actions required to complete the goal
        completion_evidence: Evidence that goal was achieved

        resolution: Runtime entity-resolution state and scope details
        activated_skills: Skills activated for this request
        information_candidates: High-signal candidates worth revisiting across steps
        execution_plan: Controller-managed plan steps for verifier-driven completion
        completed_plan_steps: Completed plan steps
        verifier_notes: Verifier notes accumulated during run
        episodic_memory: Compact high-salience memory traces from prior steps
    """

    # Core task tracking
    goal: str
    constraints: list[str] = field(default_factory=list)

    # Knowledge accumulation
    known_facts: list[str] = field(default_factory=list)
    completed_actions: list[str] = field(default_factory=list)
    pending_questions: list[str] = field(default_factory=list)

    # Progress tracking (controller-managed)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    step_count: int = 0
    repair_count: int = 0

    # Intent routing results
    intent: Optional[str] = None
    allowed_tool_groups: list[str] = field(default_factory=list)
    route_source: str = "unknown"
    route_confidence: float = 0.0
    route_confidence_tier: str = "low"
    conversational_profile: str = "main"
    tool_visibility_mode: str = "full"
    tool_visibility_escalated: bool = False
    tool_visibility_escalations_count: int = 0
    clarification_requests_count: int = 0

    # Completion tracking (clawdbot-inspired)
    goal_achieved: bool = False
    pending_actions: list[str] = field(default_factory=list)
    completion_evidence: list[str] = field(default_factory=list)

    # Runtime context managed by controller/tool handlers
    resolution: dict[str, Any] = field(default_factory=dict)
    activated_skills: list[dict[str, Any]] = field(default_factory=list)
    information_candidates: list[dict[str, Any]] = field(default_factory=list)
    execution_plan: list[str] = field(default_factory=list)
    completed_plan_steps: list[str] = field(default_factory=list)
    verifier_notes: list[str] = field(default_factory=list)
    episodic_memory: list[dict[str, Any]] = field(default_factory=list)
    ui_directives: dict[str, Any] | None = None
    request_context: dict[str, Any] = field(default_factory=dict)

    # Timestamps
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def tool_calls_count(self) -> int:
        """Total number of tool calls made."""
        return len(self.tool_calls)

    @property
    def successful_tool_calls(self) -> int:
        """Number of successful tool calls."""
        return sum(1 for tc in self.tool_calls if tc.success)

    @property
    def failed_tool_calls(self) -> int:
        """Number of failed tool calls."""
        return sum(1 for tc in self.tool_calls if not tc.success)

    @property
    def last_tool_call(self) -> Optional[ToolCallRecord]:
        """Get the most recent tool call, if any."""
        return self.tool_calls[-1] if self.tool_calls else None

    def add_fact(self, fact: str) -> None:
        """
        Add a known fact (called by controller after tool execution).

        Facts are deduplicated to avoid redundant context.
        """
        if fact and fact not in self.known_facts:
            self.known_facts.append(fact)

    def add_action(self, action: str) -> None:
        """Record a completed action."""
        if action:
            self.completed_actions.append(action)

    def add_question(self, question: str) -> None:
        """Add a pending question for the user."""
        if question and question not in self.pending_questions:
            self.pending_questions.append(question)

    def clear_questions(self) -> None:
        """Clear pending questions after they've been asked."""
        self.pending_questions.clear()

    def add_pending_action(self, action: str) -> None:
        """Add a pending action required to complete the goal."""
        if action and action not in self.pending_actions:
            self.pending_actions.append(action)

    def complete_pending_action(self, action: str) -> None:
        """Mark a pending action as completed."""
        if action in self.pending_actions:
            self.pending_actions.remove(action)
            self.completed_actions.append(action)

    def add_completion_evidence(self, evidence: str) -> None:
        """Add evidence that the goal was achieved."""
        if evidence and evidence not in self.completion_evidence:
            self.completion_evidence.append(evidence)

    def set_execution_plan(self, steps: list[str]) -> None:
        """Set or replace the controller-authored execution plan."""
        normalized = [str(step).strip() for step in steps if str(step).strip()]
        self.execution_plan = normalized
        self.completed_plan_steps = []

    def complete_plan_step(self, step: str) -> None:
        """Mark a plan step as completed once evidence exists."""
        normalized = str(step or "").strip()
        if not normalized:
            return
        if normalized not in self.execution_plan:
            return
        if normalized not in self.completed_plan_steps:
            self.completed_plan_steps.append(normalized)

    def add_verifier_note(self, note: str) -> None:
        """Track verifier notes for observability and context."""
        normalized = str(note or "").strip()
        if normalized and normalized not in self.verifier_notes:
            self.verifier_notes.append(normalized)

    def remember_episode(
        self,
        *,
        summary: str,
        source_tool: str,
        salience: float,
        related_query: str = "",
    ) -> None:
        """Persist a compact high-signal episodic memory trace."""
        normalized_summary = str(summary or "").strip()
        normalized_source = str(source_tool or "").strip() or "unknown"
        if not normalized_summary:
            return

        try:
            parsed_salience = max(0.0, min(1.0, float(salience)))
        except (TypeError, ValueError):
            parsed_salience = 0.3

        normalized_query = str(related_query or "").strip()
        for memory in self.episodic_memory:
            if memory.get("summary") != normalized_summary:
                continue
            memory["salience"] = max(float(memory.get("salience", 0.0) or 0.0), parsed_salience)
            memory["times_seen"] = int(memory.get("times_seen", 0) or 0) + 1
            memory["last_seen_step"] = self.step_count
            if normalized_query:
                memory["last_query"] = normalized_query
            self._trim_episodic_memory()
            return

        self.episodic_memory.append(
            {
                "summary": normalized_summary,
                "source_tool": normalized_source,
                "salience": parsed_salience,
                "times_seen": 1,
                "first_seen_step": self.step_count,
                "last_seen_step": self.step_count,
                "last_query": normalized_query,
            }
        )
        self._trim_episodic_memory()

    def get_episodic_hints(self, max_terms: int = 8) -> list[str]:
        """Return lightweight lexical hints extracted from top episodic memories."""
        stop_words = {
            "the",
            "and",
            "for",
            "with",
            "from",
            "that",
            "this",
            "were",
            "have",
            "into",
            "about",
            "tool",
            "result",
            "results",
        }

        memories = sorted(
            self.episodic_memory,
            key=lambda item: (
                float(item.get("salience", 0.0) or 0.0),
                int(item.get("times_seen", 0) or 0),
                int(item.get("last_seen_step", 0) or 0),
            ),
            reverse=True,
        )
        hints: list[str] = []
        seen: set[str] = set()
        for memory in memories[:MAX_EPISODIC_IN_CONTEXT]:
            text = str(memory.get("summary") or "")
            for token in text.lower().replace("/", " ").replace("-", " ").split():
                normalized = "".join(ch for ch in token if ch.isalnum())
                if len(normalized) < 4 or normalized in stop_words or normalized in seen:
                    continue
                seen.add(normalized)
                hints.append(normalized)
                if len(hints) >= max_terms:
                    return hints
        return hints

    def _trim_episodic_memory(self) -> None:
        """Keep episodic memory bounded and salience-weighted."""
        if len(self.episodic_memory) <= MAX_EPISODIC_MEMORIES:
            return

        self.episodic_memory = sorted(
            self.episodic_memory,
            key=lambda item: (
                float(item.get("salience", 0.0) or 0.0),
                int(item.get("times_seen", 0) or 0),
                int(item.get("last_seen_step", 0) or 0),
            ),
            reverse=True,
        )[:MAX_EPISODIC_MEMORIES]

    def mark_goal_achieved(self, evidence: Optional[str] = None) -> None:
        """Mark the goal as achieved with optional evidence."""
        self.goal_achieved = True
        if evidence:
            self.add_completion_evidence(evidence)

    def has_pending_actions(self) -> bool:
        """Check if there are pending actions required."""
        return len(self.pending_actions) > 0

    def record_tool_call(self, record: ToolCallRecord) -> None:
        """Record a tool call (called by controller after execution)."""
        self.tool_calls.append(record)

    def remember_information_candidate(
        self,
        kind: str,
        candidate_id: str,
        label: str = "",
        score: Any = None,
        query: str = "",
        source_tool: str = "",
        inspected: bool = False,
    ) -> None:
        """Persist and update a high-signal candidate discovered during execution."""
        normalized_kind = str(kind or "").strip().lower() or "unknown"
        normalized_id = str(candidate_id or "").strip()
        if not normalized_id:
            return

        normalized_label = str(label or "").strip()
        normalized_query = str(query or "").strip()
        normalized_source_tool = str(source_tool or "").strip()

        parsed_score: float | None = None
        if score is not None:
            try:
                parsed_score = float(score)
            except (TypeError, ValueError):
                parsed_score = None

        for existing in self.information_candidates:
            if (
                existing.get("kind") != normalized_kind
                or existing.get("candidate_id") != normalized_id
            ):
                continue
            if normalized_label and not existing.get("label"):
                existing["label"] = normalized_label
            if normalized_query:
                existing["last_query"] = normalized_query
            if normalized_source_tool:
                existing["last_source_tool"] = normalized_source_tool
            existing["times_seen"] = int(existing.get("times_seen", 0) or 0) + 1
            existing["last_seen_step"] = self.step_count
            if inspected:
                existing["inspected"] = True
                existing["inspected_step"] = self.step_count
            if parsed_score is not None:
                current_best = existing.get("best_score")
                if current_best is None or parsed_score > float(current_best):
                    existing["best_score"] = parsed_score
            self._trim_information_candidates()
            return

        self.information_candidates.append(
            {
                "kind": normalized_kind,
                "candidate_id": normalized_id,
                "label": normalized_label,
                "best_score": parsed_score,
                "times_seen": 1,
                "last_query": normalized_query,
                "last_source_tool": normalized_source_tool,
                "last_seen_step": self.step_count,
                "inspected": inspected,
                "inspected_step": self.step_count if inspected else None,
            }
        )
        self._trim_information_candidates()

    def mark_information_candidate_inspected(
        self,
        kind: str,
        candidate_id: str,
        label: str = "",
    ) -> None:
        """Mark an information candidate as inspected with detailed retrieval."""
        self.remember_information_candidate(
            kind=kind,
            candidate_id=candidate_id,
            label=label,
            inspected=True,
        )

    def get_best_information_candidate(
        self,
        *,
        inspected_only: bool = False,
        kinds: Optional[list[str]] = None,
    ) -> Optional[dict[str, Any]]:
        """Return the highest-priority remembered information candidate."""
        normalized_kinds = None
        if kinds:
            normalized_kinds = {str(kind or "").strip().lower() for kind in kinds if kind}

        candidates = [
            c for c in self.information_candidates if isinstance(c, dict) and c.get("candidate_id")
        ]
        if normalized_kinds is not None:
            candidates = [
                c
                for c in candidates
                if str(c.get("kind") or "").strip().lower() in normalized_kinds
            ]
        if inspected_only:
            candidates = [c for c in candidates if c.get("inspected")]
        if not candidates:
            return None

        def _sort_key(candidate: dict[str, Any]) -> tuple[int, float, int]:
            inspected = 1 if candidate.get("inspected") else 0
            score = candidate.get("best_score")
            try:
                parsed_score = float(score) if score is not None else -1.0
            except (TypeError, ValueError):
                parsed_score = -1.0
            seen = int(candidate.get("times_seen", 0) or 0)
            return (inspected, parsed_score, seen)

        return sorted(candidates, key=_sort_key, reverse=True)[0]

    def _trim_information_candidates(self) -> None:
        """Keep candidate memory compact to avoid prompt bloat over long runs."""
        if len(self.information_candidates) <= MAX_INFORMATION_CANDIDATES:
            return

        def _sort_key(candidate: dict[str, Any]) -> tuple[int, float, int, int]:
            inspected = 1 if candidate.get("inspected") else 0
            score = candidate.get("best_score")
            try:
                parsed_score = float(score) if score is not None else -1.0
            except (TypeError, ValueError):
                parsed_score = -1.0
            seen = int(candidate.get("times_seen", 0) or 0)
            last_seen = int(candidate.get("last_seen_step", 0) or 0)
            return (inspected, parsed_score, seen, last_seen)

        self.information_candidates = sorted(
            self.information_candidates,
            key=_sort_key,
            reverse=True,
        )[:MAX_INFORMATION_CANDIDATES]

    def get_recent_tool_calls(self, n: int = 3) -> list[ToolCallRecord]:
        """Get the N most recent tool calls."""
        return self.tool_calls[-n:] if self.tool_calls else []

    def has_repeated_calls(self, n: int = 3) -> bool:
        """
        Check if the last N tool calls are identical.

        Used for no-progress detection.
        """
        if len(self.tool_calls) < n:
            return False

        recent = self.tool_calls[-n:]
        first = recent[0]
        return all(
            tc.tool_name == first.tool_name and tc.arguments == first.arguments for tc in recent
        )

    def has_empty_result_streak(self, n: int = 3) -> bool:
        """
        Check if the last N tool calls returned empty results.

        Used for no-progress detection.
        """
        if len(self.tool_calls) < n:
            return False

        recent = self.tool_calls[-n:]
        return all(self._is_empty_result(tc.result) for tc in recent)

    def _is_empty_result(self, result: dict[str, Any]) -> bool:
        """Check if a tool result is effectively empty."""
        if "error" in result:
            return True
        if "results" in result and len(result.get("results", [])) == 0:
            return True
        if "rows" in result and len(result.get("rows", [])) == 0:
            return True
        if "count" in result and result["count"] == 0:
            return True
        return False

    def to_context_string(self) -> str:
        """
        Generate state context for injection into prompts.

        This provides the model with current state without allowing modification.
        """
        lines = [
            "CURRENT_STATE:",
            f"GOAL: {self.goal}",
            f"STEP: {self.step_count}",
            f"TOOL_CALLS_USED: {self.tool_calls_count}",
        ]

        if self.constraints:
            lines.append(f"CONSTRAINTS: {', '.join(self.constraints)}")

        if self.intent:
            lines.append(f"INTENT: {self.intent} (routing metadata only)")
            lines.append(
                "ROUTING: "
                f"source={self.route_source}, confidence={self.route_confidence:.2f}, "
                f"tier={self.route_confidence_tier}, tools={self.tool_visibility_mode}"
            )
            lines.append(f"PROFILE: {self.conversational_profile}")
            if self.tool_visibility_escalations_count:
                lines.append(
                    f"ROUTING_RECOVERY: tool_visibility_escalations={self.tool_visibility_escalations_count}"
                )
            if self.clarification_requests_count:
                lines.append(f"CLARIFICATIONS_REQUESTED: {self.clarification_requests_count}")

        if self.known_facts:
            recent_facts = self.known_facts[-8:]
            lines.append(f"KNOWN_FACTS: {'; '.join(recent_facts)}")

        if self.completed_actions:
            recent_actions = self.completed_actions[-5:]
            lines.append(f"COMPLETED: {'; '.join(recent_actions)}")

        if self.pending_actions:
            lines.append(f"PENDING_ACTIONS: {'; '.join(self.pending_actions)}")

        if self.pending_questions:
            lines.append(f"PENDING_QUESTIONS: {'; '.join(self.pending_questions)}")

        if self.execution_plan:
            plan_total = len(self.execution_plan)
            completed = len(self.completed_plan_steps)
            lines.append(f"EXECUTION_PLAN_PROGRESS: {completed}/{plan_total}")
            remaining = [
                step for step in self.execution_plan if step not in self.completed_plan_steps
            ]
            if remaining:
                lines.append("PLAN_REMAINING: " + "; ".join(remaining[:4]))

        if self.verifier_notes:
            lines.append("VERIFIER_NOTES: " + "; ".join(self.verifier_notes[-4:]))

        if self.information_candidates:

            def _safe_sort_score(candidate: dict[str, Any]) -> float:
                score = candidate.get("best_score")
                try:
                    return float(score) if score is not None else -1.0
                except (TypeError, ValueError):
                    return -1.0

            top_candidates = sorted(
                self.information_candidates,
                key=lambda c: (
                    1 if c.get("inspected") else 0,
                    _safe_sort_score(c),
                    int(c.get("times_seen", 0) or 0),
                ),
                reverse=True,
            )[:MAX_INFORMATION_CANDIDATES_IN_CONTEXT]
            serialized_candidates: list[str] = []
            for candidate in top_candidates:
                candidate_id = str(candidate.get("candidate_id") or "").strip()
                if not candidate_id:
                    continue
                kind = str(candidate.get("kind") or "unknown").strip()
                label = str(candidate.get("label") or "untitled").strip()
                inspected = "inspected" if candidate.get("inspected") else "not_inspected"
                score = candidate.get("best_score")
                try:
                    score_text = f"{float(score):.3f}" if score is not None else "n/a"
                except (TypeError, ValueError):
                    score_text = "n/a"
                serialized_candidates.append(
                    f"{kind}:{label} [{candidate_id}] ({inspected}, score={score_text})"
                )
            if serialized_candidates:
                lines.append("INFORMATION_CANDIDATES: " + "; ".join(serialized_candidates))

        if self.episodic_memory:
            top_memories = sorted(
                self.episodic_memory,
                key=lambda item: (
                    float(item.get("salience", 0.0) or 0.0),
                    int(item.get("times_seen", 0) or 0),
                    int(item.get("last_seen_step", 0) or 0),
                ),
                reverse=True,
            )[:MAX_EPISODIC_IN_CONTEXT]
            serialized = []
            for item in top_memories:
                summary = str(item.get("summary") or "").strip()
                if not summary:
                    continue
                source = str(item.get("source_tool") or "unknown")
                salience = float(item.get("salience", 0.0) or 0.0)
                serialized.append(f"{source}:{summary} (salience={salience:.2f})")
            if serialized:
                lines.append("EPISODIC_MEMORY: " + "; ".join(serialized))

        if self.request_context:
            parts: list[str] = []
            timezone = str(self.request_context.get("timezone") or "").strip()
            locale = str(self.request_context.get("locale") or "").strip()
            location = self.request_context.get("location")
            if timezone:
                parts.append(f"timezone={timezone}")
            if locale:
                parts.append(f"locale={locale}")
            if isinstance(location, dict) and "lat" in location and "lon" in location:
                parts.append("location=available")
            if parts:
                lines.append(f"REQUEST_CONTEXT: {', '.join(parts)}")

            ui_submission = self.request_context.get("ui_submission")
            if isinstance(ui_submission, dict):
                submission_parts: list[str] = []
                block_id = str(ui_submission.get("block_id") or "").strip()
                action_id = str(ui_submission.get("action_id") or "").strip()
                text_fallback = str(ui_submission.get("text_fallback") or "").strip()
                if block_id:
                    submission_parts.append(f"block_id={block_id}")
                if action_id:
                    submission_parts.append(f"action_id={action_id}")
                if text_fallback:
                    preview = text_fallback[:240]
                    if len(text_fallback) > 240:
                        preview += "..."
                    submission_parts.append(f"text_fallback={preview!r}")
                if submission_parts:
                    lines.append(f"UI_SUBMISSION: {', '.join(submission_parts)}")

        if self.goal_achieved:
            lines.append("GOAL_STATUS: ACHIEVED")
        elif self.pending_actions:
            lines.append("GOAL_STATUS: IN_PROGRESS")

        return "\n".join(lines)

    def to_metadata(self) -> dict[str, Any]:
        """
        Convert state to metadata for storage/logging.

        Compatible with existing AgentState.to_metadata() method.
        """
        return {
            "goal": self.goal,
            "intent": self.intent,
            "route_source": self.route_source,
            "route_confidence": self.route_confidence,
            "route_confidence_tier": self.route_confidence_tier,
            "conversational_profile": self.conversational_profile,
            "tool_visibility_mode": self.tool_visibility_mode,
            "tool_visibility_escalated": self.tool_visibility_escalated,
            "tool_visibility_escalations_count": self.tool_visibility_escalations_count,
            "clarification_requests_count": self.clarification_requests_count,
            "constraints": self.constraints,
            "step_count": self.step_count,
            "tool_calls_count": self.tool_calls_count,
            "repair_count": self.repair_count,
            "known_facts_count": len(self.known_facts),
            "resolution": self.resolution,
            "information_candidates_count": len(self.information_candidates),
            "execution_plan_steps": len(self.execution_plan),
            "execution_plan_completed_steps": len(self.completed_plan_steps),
            "episodic_memory_count": len(self.episodic_memory),
            "activated_skills": [s.get("name") for s in self.activated_skills],
            "has_ui_directives": bool(self.ui_directives),
        }

    def to_dict(self) -> dict[str, Any]:
        """Full serialization for logging/debugging."""
        return {
            "goal": self.goal,
            "constraints": self.constraints,
            "known_facts": self.known_facts,
            "completed_actions": self.completed_actions,
            "pending_questions": self.pending_questions,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "step_count": self.step_count,
            "repair_count": self.repair_count,
            "intent": self.intent,
            "allowed_tool_groups": self.allowed_tool_groups,
            "route_source": self.route_source,
            "route_confidence": self.route_confidence,
            "route_confidence_tier": self.route_confidence_tier,
            "conversational_profile": self.conversational_profile,
            "tool_visibility_mode": self.tool_visibility_mode,
            "tool_visibility_escalated": self.tool_visibility_escalated,
            "tool_visibility_escalations_count": self.tool_visibility_escalations_count,
            "clarification_requests_count": self.clarification_requests_count,
            "resolution": self.resolution,
            "information_candidates": self.information_candidates,
            "execution_plan": self.execution_plan,
            "completed_plan_steps": self.completed_plan_steps,
            "verifier_notes": self.verifier_notes,
            "episodic_memory": self.episodic_memory,
            "activated_skills": [s.get("name") for s in self.activated_skills],
            "ui_directives": self.ui_directives,
            "request_context": self.request_context,
            "started_at": self.started_at.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"AgentState(goal={self.goal!r}, intent={self.intent}, "
            f"steps={self.step_count}, tool_calls={self.tool_calls_count})"
        )
