"""
Main agent controller for bounded, reliable tool usage.

This is the core orchestration component that implements:
"The model proposes. The controller validates, executes, and decides when to continue or stop."

The controller:
1. Initializes state with the user's goal
2. Runs intent router for intent/hints
3. Manages the agent loop with hard limits
4. Validates tool calls before execution
5. Checks goal coverage after execution
6. Decides when to continue, ask user, or stop
"""

import contextlib
import json
import os
import re

# Import with absolute paths to avoid circular imports
import sys
import uuid
from collections.abc import AsyncGenerator
from contextvars import ContextVar
from time import perf_counter
from typing import Any, Optional

from agent.agent_interfaces import (
    ConversationalAgentInterface,
    build_default_conversational_interface,
)
from agents.registry import build_conversational_profile_registry, choose_profile_interface
from llm_config import get_smart_model
from location_inference import infer_current_place
from observability import trace
from observability.logger import get_runtime_logger
from search_normalization import normalize_search_text
from tools.action_enums import GetEventsAction

from .contact_resolution import should_pre_resolve_contacts
from .contact_scope import (
    apply_contact_resolution_result,
    block_redundant_contact_resolution,
    ensure_contact_scope,
    record_pre_resolution_outcome,
)
from .enums import (
    ConfidenceTier,
    FollowUpSource,
    LimitAction,
    ToolVisibilityMode,
)
from .guardrails import (
    detect_future_temporal_intent,
    detect_temporal_sort_order,
    extract_explicit_time_bounds,
    optimize_query_for_scoped_contacts,
    sanitize_goal_text,
    utc_now_iso,
)
from .limits import AgentConfig, LimitChecker
from .llm_transport import call_llm_with_tools, stream_llm_with_tools
from .model_routing import LLMCallPolicy, select_llm_call_policy
from .planning_policy import (
    build_execution_plan,
    build_verification_retry_prompt,
    verify_final_response,
)
from .response_guardrails import (
    CONTINUATION_PROMPT_STREAM,
    CONTINUATION_PROMPT_SYNC,
    looks_like_continuation,
)
from .router import IntentClassification, IntentRouter
from .state import AgentState
from .tool_visibility_policy import (
    confidence_tier,
    resolve_tool_visibility,
    should_escalate_tool_visibility,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = get_runtime_logger(__name__)


class AgentController:
    """
    Main controller for bounded agent execution.

    Orchestrates:
    - Intent routing
    - State management
    - Tool validation
    - Progress checking
    - Stop conditions
    """

    def __init__(
        self,
        config: Optional[AgentConfig] = None,
        intent_router: Optional[IntentRouter] = None,
        conversational_agent: ConversationalAgentInterface | None = None,
    ):
        """
        Initialize the agent controller.

        Args:
            config: Agent configuration (uses env vars if not provided)
            intent_router: Intent router (created if not provided)
            conversational_agent: Agent-specific conversational interface implementation
        """
        self.config = config or AgentConfig.from_env()
        self.intent_router = intent_router or IntentRouter()
        self.limit_checker = LimitChecker(self.config)

        # LLM configuration
        self.llm_base_url = os.getenv("LLM_BASE_URL", "")
        self.llm_model = get_smart_model()
        self.llm_api_key = os.getenv("LLM_API_KEY", "")
        self.llm_timeout = int(os.getenv("LLM_TIMEOUT", "120"))
        self.router_restriction_mode = os.getenv("ROUTER_RESTRICTION_MODE", "conservative").strip()
        self.router_high_confidence_threshold = float(
            os.getenv("ROUTER_HIGH_CONFIDENCE_THRESHOLD", "0.80")
        )
        self.router_medium_confidence_threshold = float(
            os.getenv("ROUTER_MEDIUM_CONFIDENCE_THRESHOLD", "0.60")
        )
        self.conversational_agent = conversational_agent
        if self.conversational_agent is None:
            self.conversational_agent = build_default_conversational_interface(
                max_steps=self.config.max_steps,
                max_tool_calls=self.config.max_tool_calls,
                timeout_seconds=self.llm_timeout,
            )
        base_interface = self.conversational_agent
        if base_interface is None:
            raise RuntimeError("No conversational agent interface provided to AgentController")
        self._profile_registry: dict[str, ConversationalAgentInterface] = (
            build_conversational_profile_registry(
                max_steps=self.config.max_steps,
                max_tool_calls=self.config.max_tool_calls,
                timeout_seconds=self.llm_timeout,
            )
        )
        # Controller is process-global in llm.py and can serve concurrent async requests.
        # Keep selected conversational profile request-scoped to avoid cross-request leakage.
        self._agent_interface_context: ContextVar[ConversationalAgentInterface | None] = ContextVar(
            "agent_interface_context",
            default=base_interface,
        )
        self.agent_profile = base_interface.profile
        self.runtime_profile = self.agent_profile.runtime

        # Lazy-loaded components
        self._tool_registry = None
        self._pre_validator = None
        self._post_validator = None
        self._logger = None
        self._tool_executor = None
        self._active_llm_policy: LLMCallPolicy | None = None
        self._last_llm_policy: LLMCallPolicy | None = None

    @property
    def tool_registry(self):
        """Lazy-load tool registry."""
        if self._tool_registry is None:
            from tools.registry import get_registry

            self._tool_registry = get_registry()
        return self._tool_registry

    @property
    def pre_validator(self):
        """Lazy-load pre-execution validator."""
        if self._pre_validator is None:
            from tools.validators import PreExecutionValidator

            self._pre_validator = PreExecutionValidator(
                self.tool_registry,
                max_repairs=self.config.max_repairs,
            )
        return self._pre_validator

    @property
    def post_validator(self):
        """Lazy-load post-execution validator."""
        if self._post_validator is None:
            from tools.validators import PostExecutionValidator

            self._post_validator = PostExecutionValidator()
        return self._post_validator

    @property
    def goal_validator(self):
        """Lazy-load goal completion validator."""
        if not hasattr(self, "_goal_validator") or self._goal_validator is None:
            from tools.validators.post_execution import GoalCompletionValidator

            self._goal_validator = GoalCompletionValidator()
        return self._goal_validator

    @property
    def logger(self):
        """Lazy-load agent logger."""
        if self._logger is None:
            from observability.logger import get_logger

            self._logger = get_logger()
        return self._logger

    @property
    def tool_executor(self):
        """Lazy-load tool execution coordinator."""
        if self._tool_executor is None:
            from .tool_executor import ToolExecutionCoordinator

            self._tool_executor = ToolExecutionCoordinator(self)
        return self._tool_executor

    def _agent_interface(self) -> ConversationalAgentInterface:
        """Return bound conversational agent interface or raise clear error."""
        current_interface = self._agent_interface_context.get()
        if current_interface is not None:
            return current_interface
        if self.conversational_agent is None:
            raise RuntimeError("No conversational agent interface provided to AgentController")
        return self.conversational_agent

    def _resolve_agent_interface(
        self,
        classification: IntentClassification | None,
    ) -> ConversationalAgentInterface:
        """Resolve conversational profile from routed intent metadata."""
        return choose_profile_interface(classification, self._profile_registry)

    async def _prepare_execution_context(
        self,
        *,
        state: AgentState,
        question: str,
        conversation_history: Optional[list[dict[str, str]]],
        user_email: Optional[str],
        search_limit: int,
        run_id: str,
    ) -> tuple[
        Optional[IntentClassification], Optional[str], list[dict[str, Any]], list[dict[str, Any]]
    ]:
        """Prepare routing, pre-resolution, messages, and initial tool visibility."""
        classification: Optional[IntentClassification] = None
        if self.config.enable_intent_routing:
            classification = await self._run_intent_router(question, conversation_history, run_id)
            self._apply_classification_to_state(state, classification)

        selected_interface = self._resolve_agent_interface(classification)
        self._agent_interface_context.set(selected_interface)
        self.agent_profile = selected_interface.profile
        self.runtime_profile = selected_interface.profile.runtime
        state.conversational_profile = selected_interface.profile.name

        clarification_prompt: Optional[str] = None
        pre_resolve_hint = (
            classification.pre_resolve_contacts if classification is not None else None
        )
        if should_pre_resolve_contacts(state.intent, pre_resolve_hint):
            clarification_prompt = self._prime_contact_scope_for_question(
                state=state,
                question=question,
                user_email=user_email,
                conversation_history=conversation_history,
            )
        if clarification_prompt:
            return classification, clarification_prompt, [], []

        tools, visibility_mode, selected_groups = self._resolve_tool_visibility(classification)
        state.tool_visibility_mode = visibility_mode.value
        state.allowed_tool_groups = selected_groups
        messages = self._build_messages(
            question,
            state,
            conversation_history,
            user_email,
            search_limit,
            state.request_context,
        )
        return classification, None, messages, tools

    def _check_limits_and_recovery(
        self,
        *,
        state: AgentState,
        tools: list[dict[str, Any]],
    ) -> tuple[LimitAction, list[dict[str, Any]], Any | None, str | None]:
        """Run hard/no-progress checks and attempt recovery escalation when possible."""
        hard_violation = self.limit_checker.check(state)
        if hard_violation:
            return LimitAction.VIOLATION, tools, hard_violation, None

        no_progress_violation = self.limit_checker.detect_no_progress(state)
        if no_progress_violation:
            if self._should_escalate_tool_visibility(state, no_progress_violation):
                escalated_tools = self._escalate_tool_visibility(
                    state=state,
                    reason=no_progress_violation.message,
                )
                return LimitAction.ESCALATED, escalated_tools, None, no_progress_violation.message
            return LimitAction.VIOLATION, tools, no_progress_violation, None

        return LimitAction.OK, tools, None, None

    def _start_run(
        self, question: str, user_id: str, session_id: Optional[str]
    ) -> tuple[float, str]:
        """Initialize run-level logging and tracing shared by sync/stream paths."""
        total_start = perf_counter()
        base_interface = self.conversational_agent
        if base_interface is None:
            raise RuntimeError("No conversational agent interface provided to AgentController")
        self._agent_interface_context.set(base_interface)
        self.agent_profile = base_interface.profile
        self.runtime_profile = base_interface.profile.runtime
        run_id = self.logger.start_run(question, user_id, session_id)
        self._active_llm_policy = None
        self._last_llm_policy = None
        trace.trace_run_start(question, run_id)
        return total_start, run_id

    def _initialize_state(
        self,
        *,
        question: str,
        client_context: Optional[dict[str, Any]],
        ui_submission: Optional[dict[str, Any]],
        response_modality: Optional[str] = None,
    ) -> AgentState:
        """Build the canonical AgentState shared by sync/stream paths."""
        state = AgentState(goal=question)
        state.request_context = self._normalize_client_context(client_context)
        normalized_submission = self._normalize_ui_submission(ui_submission)
        if normalized_submission:
            state.request_context["ui_submission"] = normalized_submission
        if response_modality:
            from voice_response import normalize_modality

            state.request_context["response_modality"] = normalize_modality(response_modality).value
        state.conversational_profile = self.runtime_profile.name
        return state

    def _inject_inferred_location(
        self,
        *,
        state: AgentState,
        user_email: str | None,
    ) -> None:
        """Attach inferred place metadata into request context when possible."""
        location = state.request_context.get("location")
        if not isinstance(location, dict):
            return
        try:
            inferred = infer_current_place(location, user_email=user_email)
        except Exception as exc:
            logger.warning("[location_inference] failed to infer location: %s", exc)
            return
        if isinstance(inferred, dict):
            state.request_context["inferred_location"] = inferred

    def _log_selected_profile(self) -> None:
        """Log active conversational profile metadata for current run."""
        interface = self._agent_interface()
        runtime = interface.profile.runtime
        trace.trace_decision(
            "Agent profile selected",
            interface.profile.name,
            {
                "max_steps": runtime.max_steps,
                "max_tool_calls": runtime.max_tool_calls,
            },
        )

    def _begin_step(
        self,
        *,
        state: AgentState,
        tools: list[dict[str, Any]],
        run_id: str,
    ) -> tuple[LimitAction, list[dict[str, Any]], Any | None, str | None]:
        """Run limit checks and start the next step when safe to continue."""
        trace.trace_limit_check(
            state.step_count,
            self.config.max_steps,
            state.tool_calls_count,
            self.config.max_tool_calls,
            state.repair_count,
            self.config.max_repairs,
        )
        limit_action, tools, violation, escalation_reason = self._check_limits_and_recovery(
            state=state,
            tools=tools,
        )
        if limit_action is not LimitAction.OK:
            return limit_action, tools, violation, escalation_reason

        state.step_count += 1
        trace.trace_step_start(
            state.step_count,
            state.tool_calls_count,
            len(state.known_facts),
        )
        self.logger.start_step(run_id, state.step_count)
        return limit_action, tools, violation, escalation_reason

    def _consume_follow_up_prompt(
        self,
        *,
        state: AgentState,
        source: str,
        stream: bool,
    ) -> str | None:
        """Return user-facing follow-up prompt and apply shared bookkeeping/logging."""
        follow_up_prompt, follow_up_source = (
            self._agent_interface().get_follow_up_prompt_from_state(state)
        )
        if not follow_up_prompt:
            return None

        suffix = " (stream)" if stream else ""
        if follow_up_source is FollowUpSource.CONTACT_CLARIFICATION:
            trace.trace_contact_resolution_outcome(
                "clarification_returned",
                {"source": source},
            )
            trace.trace_decision(
                f"Need user clarification{suffix}",
                "Returning clarification prompt to user",
                {"prompt": follow_up_prompt},
            )
            state.clarification_requests_count += 1
            return follow_up_prompt

        if follow_up_source is FollowUpSource.UI_FOLLOW_UP:
            trace.trace_decision(
                f"Returning UI follow-up{suffix}",
                "Structured directive requires user input",
                {"prompt": follow_up_prompt},
            )
            state.clarification_requests_count += 1
            return follow_up_prompt

        return None

    async def run(
        self,
        question: str,
        user_id: str = "default_user",
        session_id: Optional[str] = None,
        user_email: Optional[str] = None,
        conversation_history: Optional[list[dict[str, str]]] = None,
        search_limit: int = 30,
        client_context: Optional[dict[str, Any]] = None,
        ui_submission: Optional[dict[str, Any]] = None,
        response_modality: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Run the agent loop for a question.

        This is the main entry point for the bounded agent.

        Args:
            question: The user's question
            user_id: User identifier
            session_id: Optional session/thread ID
            user_email: User's email for context
            conversation_history: Previous messages
            search_limit: Max search results
            client_context: Client-provided timezone/locale/location context
            ui_submission: Optional structured UI submission from the client

        Returns:
            Response bundle with answer and metadata
        """
        total_start, run_id = self._start_run(question, user_id, session_id)
        state = self._initialize_state(
            question=question,
            client_context=client_context,
            ui_submission=ui_submission,
            response_modality=response_modality,
        )
        self._inject_inferred_location(state=state, user_email=user_email)

        try:
            _, clarification_prompt, messages, tools = await self._prepare_execution_context(
                state=state,
                question=question,
                conversation_history=conversation_history,
                user_email=user_email,
                search_limit=search_limit,
                run_id=run_id,
            )
            self._log_selected_profile()
            self._initialize_execution_plan(state, question)
            if clarification_prompt:
                trace.trace_contact_resolution_outcome(
                    "clarification_returned",
                    {"source": "pre_resolution"},
                )
                state.clarification_requests_count += 1
                return self._finalize(
                    question,
                    clarification_prompt,
                    state,
                    run_id,
                    session_id,
                    total_start,
                )

            # Phase 2: Agent loop
            while True:
                limit_action, tools, violation, _ = self._begin_step(
                    state=state,
                    tools=tools,
                    run_id=run_id,
                )
                if limit_action is LimitAction.ESCALATED:
                    continue
                if limit_action is LimitAction.VIOLATION and violation is not None:
                    return self._handle_limit_violation(
                        state,
                        violation,
                        run_id,
                        session_id,
                        total_start,
                    )

                # Call LLM
                trace.trace_llm_request(len(tools))
                llm_start = perf_counter()
                self._set_active_llm_policy(state=state, question=question, tools_count=len(tools))
                response = self._call_llm(messages, tools)
                llm_duration = (perf_counter() - llm_start) * 1000

                message = response.get("message", {})
                content = (message.get("content") or "").strip()
                reasoning = str(message.get("reasoning") or "").strip()
                tool_calls = message.get("tool_calls") or []

                # Trace LLM response
                trace.trace_llm_response(
                    llm_duration,
                    has_tool_calls=bool(tool_calls),
                    tool_count=len(tool_calls),
                    content_preview=content if not tool_calls else None,
                    reasoning_preview=reasoning or None,
                )
                self.logger.log_llm_call(
                    run_id,
                    state.step_count,
                    llm_duration,
                    content=content,
                    reasoning=reasoning or None,
                    had_tool_calls=bool(tool_calls),
                )

                # Handle tool calls
                if tool_calls:
                    await self._handle_tool_calls(
                        tool_calls,
                        state,
                        messages,
                        question,
                        search_limit,
                        run_id,
                        user_email=user_email,
                        conversation_history=conversation_history,
                    )

                    follow_up_prompt = self._consume_follow_up_prompt(
                        state=state,
                        source="tool_loop",
                        stream=False,
                    )
                    if follow_up_prompt:
                        # If the LLM also produced text content alongside tool
                        # calls (e.g. a conversational answer + UI follow-up
                        # buttons), prefer the text as the answer. The
                        # ui_directives are included in the bundle via state.
                        answer = content if content else follow_up_prompt
                        return self._finalize(
                            question,
                            answer,
                            state,
                            run_id,
                            session_id,
                            total_start,
                        )

                    # Check if goal was achieved after tool execution
                    goal_check = self._check_goal_completion(state, "")
                    if goal_check["pending_actions"]:
                        # Track pending actions but don't inject prompt yet
                        # Let the LLM decide on next step
                        for action in goal_check["pending_actions"]:
                            state.add_pending_action(action)
                        trace.trace_decision(
                            "Goal not yet achieved",
                            goal_check["reason"],
                            {"pending_actions": goal_check["pending_actions"]},
                        )

                    if self._should_escalate_tool_visibility(state):
                        tools = self._escalate_tool_visibility(
                            state=state,
                            reason="Restricted tools produced repeated failures or empty results",
                        )
                    self._update_plan_progress(state)
                    continue

                # Handle empty content
                if not content:
                    trace.trace_empty_response(state.step_count)
                    if state.step_count < 3:
                        messages.append(
                            {
                                "role": "user",
                                "content": "Please continue and provide your response.",
                            }
                        )
                        continue
                    # Give up after a few empty responses
                    content = self._format_unable_to_complete_message(
                        "agent returned empty responses repeatedly"
                    )

                # Check if this is a continuation intent
                if (
                    looks_like_continuation(content)
                    and state.step_count < self.config.max_steps - 1
                ):
                    trace.trace_continuation_detected(content)
                    self.logger.log_continuation_detected(content)
                    messages.append(
                        {
                            "role": "user",
                            "content": CONTINUATION_PROMPT_SYNC,
                        }
                    )
                    continue

                # Check for malformed tool call (JSON in content instead of proper tool call)
                malformed_prompt, malformed_reason = (
                    self._agent_interface().classify_malformed_output(content)
                )
                if malformed_prompt and state.step_count < self.config.max_steps - 1:
                    trace.trace_malformed_output(
                        content, malformed_reason or "Malformed tool output"
                    )
                    self.logger.log_malformed_output(
                        content, malformed_reason or "Malformed tool output"
                    )
                    messages.append({"role": "user", "content": malformed_prompt})
                    continue

                # CRITICAL: Check if goal was actually achieved before returning
                # This prevents premature termination (clawdbot pattern)
                goal_check = self._check_goal_completion(state, content)
                if (
                    not goal_check["achieved"]
                    and goal_check["pending_actions"]
                    and state.step_count < self.config.max_steps - 1
                ):
                    if await self._execute_pending_completion_action(
                        pending_action=goal_check["pending_actions"][0],
                        state=state,
                        messages=messages,
                        question=question,
                        search_limit=search_limit,
                        run_id=run_id,
                        user_email=user_email,
                        conversation_history=conversation_history,
                    ):
                        continue
                    trace.trace_decision(
                        "Preventing premature completion",
                        "Goal not achieved, forcing continuation",
                        {"pending_actions": goal_check["pending_actions"]},
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": self._agent_interface().build_force_completion_prompt(
                                goal_check
                            ),
                        }
                    )
                    continue

                verified, verify_reason, missing_actions = verify_final_response(
                    final_content=content,
                    goal_check=goal_check,
                    completion_evidence=state.completion_evidence,
                    tool_calls_count=state.tool_calls_count,
                )
                if not verified and state.step_count < self.config.max_steps - 1:
                    state.add_verifier_note(verify_reason)
                    messages.append(
                        {
                            "role": "user",
                            "content": build_verification_retry_prompt(
                                verify_reason,
                                missing_actions,
                            ),
                        }
                    )
                    continue

                # Final answer
                messages.append(message)
                return self._finalize(
                    question,
                    content,
                    state,
                    run_id,
                    session_id,
                    total_start,
                )

        except Exception as e:
            trace.trace_run_error(run_id, str(e))
            self.logger.complete_run(run_id, success=False, error=str(e))
            raise

    async def run_stream(
        self,
        question: str,
        user_id: str = "default_user",
        session_id: Optional[str] = None,
        user_email: Optional[str] = None,
        conversation_history: Optional[list[dict[str, str]]] = None,
        search_limit: int = 30,
        client_context: Optional[dict[str, Any]] = None,
        ui_submission: Optional[dict[str, Any]] = None,
        response_modality: Optional[str] = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Stream agent responses with tool calling support.

        Yields events similar to the original answer_question_stream.
        """
        total_start, run_id = self._start_run(question, user_id, session_id)
        state = self._initialize_state(
            question=question,
            client_context=client_context,
            ui_submission=ui_submission,
            response_modality=response_modality,
        )
        self._inject_inferred_location(state=state, user_email=user_email)

        try:
            _, clarification_prompt, messages, tools = await self._prepare_execution_context(
                state=state,
                question=question,
                conversation_history=conversation_history,
                user_email=user_email,
                search_limit=search_limit,
                run_id=run_id,
            )
            self._log_selected_profile()
            self._initialize_execution_plan(state, question)
            if clarification_prompt:
                trace.trace_contact_resolution_outcome(
                    "clarification_returned",
                    {"source": "pre_resolution_stream"},
                )
                state.clarification_requests_count += 1
                bundle = self._finalize(
                    question,
                    clarification_prompt,
                    state,
                    run_id,
                    session_id,
                    total_start,
                )
                yield {"type": "done", "bundle": bundle}
                return

            accumulated_content = ""

            while True:
                limit_action, tools, violation, escalation_reason = self._begin_step(
                    state=state,
                    tools=tools,
                    run_id=run_id,
                )
                if limit_action is LimitAction.ESCALATED:
                    yield {
                        "type": "status",
                        "message": escalation_reason
                        or "Expanding tool access to recover from no-progress.",
                    }
                    continue
                if limit_action is LimitAction.VIOLATION and violation is not None:
                    trace.trace_limit_violation(
                        violation.limit_type.value,
                        violation.message,
                        {"steps": state.step_count, "tool_calls": state.tool_calls_count},
                    )
                    yield {
                        "type": "status",
                        "message": f"Limit reached: {violation.message}",
                    }
                    bundle = self._handle_limit_violation(
                        state,
                        violation,
                        run_id,
                        session_id,
                        total_start,
                    )
                    yield {"type": "done", "bundle": bundle}
                    return
                yield {"type": "status", "message": f"Thinking (step {state.step_count})..."}

                tool_calls = []
                current_content = ""
                current_reasoning = ""
                streamed_any = False
                self._set_active_llm_policy(state=state, question=question, tools_count=len(tools))
                trace.trace_llm_request(len(tools))
                llm_start = perf_counter()

                async for chunk in self._stream_llm(messages, tools):
                    message = chunk.get("message", {})
                    delta = message.get("content", "")

                    if delta:
                        current_content += delta
                        yield {"type": "token", "content": delta}
                        streamed_any = True

                    reasoning_delta = str(message.get("reasoning") or "")
                    if reasoning_delta:
                        current_reasoning += reasoning_delta

                    chunk_tools = message.get("tool_calls")
                    if chunk_tools:
                        tool_calls.extend(chunk_tools)

                    if chunk.get("done"):
                        break

                llm_duration = (perf_counter() - llm_start) * 1000
                final_content_preview = current_content.strip() if not tool_calls else None
                trace.trace_llm_response(
                    llm_duration,
                    has_tool_calls=bool(tool_calls),
                    tool_count=len(tool_calls),
                    content_preview=final_content_preview,
                    reasoning_preview=current_reasoning.strip() or None,
                )
                self.logger.log_llm_call(
                    run_id,
                    state.step_count,
                    llm_duration,
                    content=final_content_preview,
                    reasoning=current_reasoning.strip() or None,
                    had_tool_calls=bool(tool_calls),
                )

                if tool_calls:
                    # When the only tool call is emit_ui_directive and we
                    # already streamed a text answer, keep the content visible
                    # so the directives are supplementary (follow-up buttons).
                    ui_only_tool_calls = all(
                        (call.get("function") or {}).get("name") == "emit_ui_directive"
                        for call in tool_calls
                    )
                    if streamed_any and not ui_only_tool_calls:
                        yield {"type": "clear_content"}

                    messages.append(
                        {
                            "role": "assistant",
                            "content": current_content,
                            "tool_calls": tool_calls,
                        }
                    )

                    for call in tool_calls:
                        func = call.get("function", {})
                        func_name = func.get("name", "unknown")
                        func_args = func.get("arguments", {})

                        yield {
                            "type": "tool_call",
                            "name": func_name,
                            "args": self._normalize_stream_tool_args(func_args),
                        }

                        result = await self._execute_tool_call(
                            call,
                            state,
                            question,
                            search_limit,
                            run_id,
                            user_email=user_email,
                            conversation_history=conversation_history,
                        )

                        yield {
                            "type": "tool_result",
                            "name": func_name,
                            "result": result,
                        }

                        function = call.get("function", {})
                        raw_args = function.get("arguments", {}) if isinstance(function, dict) else {}
                        if isinstance(raw_args, str):
                            try:
                                args = json.loads(raw_args)
                            except json.JSONDecodeError:
                                args = None
                        else:
                            args = raw_args if isinstance(raw_args, dict) else None

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.get("id"),
                                "content": json.dumps(
                                    self.tool_executor.build_tool_message_payload(
                                        func_name,
                                        result,
                                        args=args if isinstance(args, dict) else None,
                                        messages=messages,
                                    ),
                                    ensure_ascii=False,
                                    default=str,
                                ),
                            }
                        )

                    follow_up_prompt = self._consume_follow_up_prompt(
                        state=state,
                        source="tool_loop_stream",
                        stream=True,
                    )
                    if follow_up_prompt:
                        # If the LLM already streamed a text answer alongside
                        # the UI directive, keep that as the accumulated
                        # content. The ui_directives are in state and will be
                        # included in the final bundle.
                        if current_content.strip():
                            accumulated_content = current_content
                        else:
                            accumulated_content = follow_up_prompt
                            yield {"type": "token", "content": follow_up_prompt}
                        break

                    if self._should_escalate_tool_visibility(state):
                        tools = self._escalate_tool_visibility(
                            state=state,
                            reason="Restricted tools produced repeated failures or empty results",
                        )
                        yield {
                            "type": "status",
                            "message": "Expanding tool access to improve recovery.",
                        }

                    self._update_plan_progress(state)

                    continue

                if not current_content.strip():
                    messages.append(
                        {
                            "role": "user",
                            "content": "Please continue and provide your response.",
                        }
                    )
                    continue

                if (
                    looks_like_continuation(current_content)
                    and state.step_count < self.config.max_steps - 1
                ):
                    trace.trace_continuation_detected(current_content)
                    self.logger.log_continuation_detected(current_content)
                    if streamed_any:
                        yield {"type": "clear_content"}
                    messages.append(
                        {
                            "role": "user",
                            "content": CONTINUATION_PROMPT_STREAM,
                        }
                    )
                    continue

                # Check for malformed tool call (JSON in content instead of proper tool call)
                malformed_prompt, malformed_reason = (
                    self._agent_interface().classify_malformed_output(current_content)
                )
                if malformed_prompt and state.step_count < self.config.max_steps - 1:
                    trace.trace_malformed_output(
                        current_content, malformed_reason or "Malformed tool output"
                    )
                    self.logger.log_malformed_output(
                        current_content, malformed_reason or "Malformed tool output"
                    )
                    if streamed_any:
                        yield {"type": "clear_content"}
                    messages.append({"role": "user", "content": malformed_prompt})
                    continue

                # CRITICAL: Check if goal was actually achieved before returning
                goal_check = self._check_goal_completion(state, current_content)
                if (
                    not goal_check["achieved"]
                    and goal_check["pending_actions"]
                    and state.step_count < self.config.max_steps - 1
                ):
                    if await self._execute_pending_completion_action_stream(
                        pending_action=goal_check["pending_actions"][0],
                        state=state,
                        messages=messages,
                        question=question,
                        search_limit=search_limit,
                        run_id=run_id,
                        user_email=user_email,
                        conversation_history=conversation_history,
                    ):
                        yield {"type": "status", "message": "Completing action..."}
                        continue
                    trace.trace_decision(
                        "Preventing premature completion (stream)",
                        "Goal not achieved, forcing continuation",
                        {"pending_actions": goal_check["pending_actions"]},
                    )
                    if streamed_any:
                        yield {"type": "clear_content"}
                    yield {"type": "status", "message": "Completing action..."}
                    messages.append(
                        {
                            "role": "user",
                            "content": self._agent_interface().build_force_completion_prompt(
                                goal_check
                            ),
                        }
                    )
                    continue

                verified, verify_reason, missing_actions = verify_final_response(
                    final_content=current_content,
                    goal_check=goal_check,
                    completion_evidence=state.completion_evidence,
                    tool_calls_count=state.tool_calls_count,
                )
                if not verified and state.step_count < self.config.max_steps - 1:
                    state.add_verifier_note(verify_reason)
                    if streamed_any:
                        yield {"type": "clear_content"}
                    yield {
                        "type": "status",
                        "message": "Verifying evidence before final response...",
                    }
                    messages.append(
                        {
                            "role": "user",
                            "content": build_verification_retry_prompt(
                                verify_reason,
                                missing_actions,
                            ),
                        }
                    )
                    continue

                accumulated_content = current_content
                final_preview = accumulated_content.strip().replace("\n", " ")[:220]
                if final_preview:
                    trace.trace_decision(
                        "Stream final response drafted",
                        "LLM produced a final user-facing answer",
                        {"preview": final_preview},
                    )
                break

            # Finalize
            bundle = self._finalize(
                question,
                accumulated_content,
                state,
                run_id,
                session_id,
                total_start,
            )

            yield {"type": "done", "bundle": bundle}

        except Exception as e:
            trace.trace_run_error(run_id, str(e))
            self.logger.complete_run(run_id, success=False, error=str(e))
            yield {"type": "error", "message": str(e)}

    async def _run_intent_router(
        self,
        question: str,
        conversation_history: Optional[list[dict[str, str]]],
        run_id: str,
    ) -> IntentClassification:
        """Run intent classification."""
        router_start = perf_counter()
        classification = await self.intent_router.classify(question, conversation_history)
        router_duration = (perf_counter() - router_start) * 1000

        self.logger.log_intent(
            run_id,
            classification.intent.value,
            classification.allowed_tool_groups,
        )

        route_tier = self._confidence_tier(classification.confidence)
        trace.trace_decision(
            "Routing decision",
            (
                f"source={classification.route_source.value}, tier={route_tier.value}, "
                f"confidence={classification.confidence:.2f}"
            ),
            {
                "intent": classification.intent.value,
                "allowed_tool_groups": classification.allowed_tool_groups,
                "profile_selection": choose_profile_interface(
                    classification, self._profile_registry
                ).name,
            },
        )

        logger.info(
            "[controller] Intent: %s (source=%s, confidence=%.2f, duration=%.1fms)",
            classification.intent.value,
            classification.route_source.value,
            classification.confidence,
            router_duration,
        )

        return classification

    def _confidence_tier(self, confidence: float) -> ConfidenceTier:
        """Map routing confidence to high/medium/low tiers."""
        return confidence_tier(
            confidence,
            high_threshold=self.router_high_confidence_threshold,
            medium_threshold=self.router_medium_confidence_threshold,
        )

    def _apply_classification_to_state(
        self,
        state: AgentState,
        classification: IntentClassification,
    ) -> None:
        """Persist router metadata on state for downstream policy and observability."""
        state.intent = classification.intent.value
        state.allowed_tool_groups = classification.allowed_tool_groups
        state.constraints = classification.constraints
        state.route_source = classification.route_source.value
        state.route_confidence = classification.confidence
        state.route_confidence_tier = self._confidence_tier(classification.confidence).value

    def _resolve_tool_visibility(
        self,
        classification: Optional[IntentClassification],
    ) -> tuple[list[dict[str, Any]], ToolVisibilityMode, list[str]]:
        """Choose visible tools based on routing confidence and policy mode."""
        return resolve_tool_visibility(
            tool_registry=self.tool_registry,
            classification=classification,
            restriction_mode=self.router_restriction_mode,
            high_threshold=self.router_high_confidence_threshold,
            medium_threshold=self.router_medium_confidence_threshold,
        )

    def _should_escalate_tool_visibility(
        self,
        state: AgentState,
        violation: Any | None = None,
    ) -> bool:
        """Decide whether restricted tool visibility should be widened."""
        return should_escalate_tool_visibility(state=state, violation=violation)

    def _escalate_tool_visibility(
        self,
        state: AgentState,
        reason: str,
    ) -> list[dict[str, Any]]:
        """Escalate visibility to full toolset and log decision."""
        state.tool_visibility_mode = ToolVisibilityMode.FULL.value
        state.tool_visibility_escalated = True
        state.tool_visibility_escalations_count += 1
        state.allowed_tool_groups = list(self.tool_registry.list_groups())
        trace.trace_decision(
            "Escalating tool visibility",
            reason,
            {"step": state.step_count, "route_tier": state.route_confidence_tier},
        )
        return self.tool_registry.get_tool_definitions()

    def _initialize_execution_plan(self, state: AgentState, question: str) -> None:
        """Initialize controller-managed execution plan for verifier loop."""
        if state.execution_plan:
            return
        plan = build_execution_plan(question, state.intent)
        state.set_execution_plan(plan)
        self._update_plan_progress(state)

    def _update_plan_progress(self, state: AgentState) -> None:
        """Mark plan steps complete based on current evidence and tool history."""
        if not state.execution_plan:
            return
        if state.intent or state.route_confidence > 0:
            for step in state.execution_plan:
                if "clarify scope" in step.lower():
                    state.complete_plan_step(step)
                    break

        if state.tool_calls_count > 0:
            for step in state.execution_plan:
                lowered = step.lower()
                if "collect" in lowered or "gather" in lowered or "execute" in lowered:
                    state.complete_plan_step(step)

        if state.completion_evidence:
            for step in state.execution_plan:
                lowered = step.lower()
                if "verify" in lowered or "cross-check" in lowered or "synthesize" in lowered:
                    state.complete_plan_step(step)

    def _set_active_llm_policy(self, *, state: AgentState, question: str, tools_count: int) -> None:
        """Resolve adaptive model/timeout policy for the current LLM turn."""
        policy = select_llm_call_policy(
            question=question,
            state=state,
            tools_count=tools_count,
            default_model=self.llm_model or None,
            default_timeout=self.llm_timeout,
        )
        self._active_llm_policy = policy
        self._last_llm_policy = policy
        trace.trace_decision(
            "LLM routing policy",
            f"profile={policy.profile}",
            {
                "model": policy.model,
                "timeout": policy.timeout,
                "rationale": policy.rationale,
                "reasoning_effort": policy.reasoning_effort,
            },
        )

    def _normalize_client_context(
        self,
        client_context: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        """Normalize client context into a compact, safe structure for prompts."""
        if not isinstance(client_context, dict):
            return {}

        normalized: dict[str, Any] = {}

        timezone_name = str(client_context.get("timezone") or "").strip()
        if timezone_name:
            normalized["timezone"] = timezone_name

        locale = str(client_context.get("locale") or "").strip()
        if locale:
            normalized["locale"] = locale

        location = client_context.get("location")
        if isinstance(location, dict):
            try:
                raw_lat = location.get("lat")
                raw_lon = location.get("lon")
                if raw_lat is None or raw_lon is None:
                    return normalized
                lat = round(float(str(raw_lat)), 3)
                lon = round(float(str(raw_lon)), 3)
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    normalized_location: dict[str, Any] = {"lat": lat, "lon": lon}

                    accuracy = location.get("accuracy_m")
                    if accuracy is not None:
                        with contextlib.suppress(TypeError, ValueError):
                            normalized_location["accuracy_m"] = round(float(str(accuracy)), 1)

                    captured_at = str(location.get("captured_at") or "").strip()
                    if captured_at:
                        normalized_location["captured_at"] = captured_at

                    source = str(location.get("source") or "").strip()
                    if source:
                        normalized_location["source"] = source

                    normalized["location"] = normalized_location
            except (TypeError, ValueError):
                pass

        recent_place = client_context.get("recent_resolved_place")
        if isinstance(recent_place, dict):
            place_id = str(recent_place.get("place_id") or "").strip()
            if place_id:
                normalized_recent_place: dict[str, Any] = {"place_id": place_id}
                for field_name in (
                    "place_name",
                    "address",
                    "city",
                    "country",
                    "role_hint",
                    "source",
                ):
                    value = str(recent_place.get(field_name) or "").strip()
                    if value:
                        normalized_recent_place[field_name] = value
                normalized["recent_resolved_place"] = normalized_recent_place

        return normalized

    def _normalize_ui_submission(
        self,
        ui_submission: Optional[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Normalize `ui_submission` payload for prompt injection."""
        if ui_submission is None:
            return None

        from ui_dsl.validator import sanitize_ui_submission_payload

        normalized, _errors = sanitize_ui_submission_payload(ui_submission)
        return normalized

    def _build_messages(
        self,
        question: str,
        state: AgentState,
        conversation_history: Optional[list[dict[str, str]]],
        user_email: Optional[str],
        search_limit: int,
        client_context: Optional[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build the message list for the main LLM run."""
        return self._agent_interface().build_messages(
            question,
            state,
            conversation_history,
            user_email,
            search_limit,
            client_context,
        )

    def _call_llm(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Make synchronous LLM call."""
        policy = self._active_llm_policy
        model = policy.model if policy else (self.llm_model or None)
        timeout = policy.timeout if policy else self.llm_timeout
        reasoning_effort = policy.reasoning_effort if policy else "low"
        return call_llm_with_tools(
            messages,
            tools,
            model=model,
            timeout=timeout,
            reasoning_effort=reasoning_effort,
        )

    async def _stream_llm(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream LLM responses."""
        policy = self._active_llm_policy
        model = policy.model if policy else (self.llm_model or None)
        timeout = policy.timeout if policy else self.llm_timeout
        reasoning_effort = policy.reasoning_effort if policy else "low"
        async for chunk in stream_llm_with_tools(
            messages,
            tools,
            model=model,
            timeout=timeout,
            reasoning_effort=reasoning_effort,
        ):
            yield chunk

    async def _handle_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        state: AgentState,
        messages: list[dict[str, Any]],
        question: str,
        search_limit: int,
        run_id: str,
        user_email: Optional[str] = None,
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> None:
        """Handle tool calls with validation."""
        await self.tool_executor.handle_tool_calls(
            tool_calls=tool_calls,
            state=state,
            messages=messages,
            question=question,
            search_limit=search_limit,
            run_id=run_id,
            user_email=user_email,
            conversation_history=conversation_history,
        )

    async def _execute_tool_call(
        self,
        call: dict[str, Any],
        state: AgentState,
        question: str,
        search_limit: int,
        run_id: str,
        user_email: Optional[str] = None,
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> dict[str, Any]:
        """Execute a single tool call with validation."""
        return await self.tool_executor.execute_tool_call(
            call=call,
            state=state,
            question=question,
            search_limit=search_limit,
            run_id=run_id,
            user_email=user_email,
            conversation_history=conversation_history,
        )

    def _normalize_stream_tool_args(self, raw_args: Any) -> dict[str, Any]:
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            text = raw_args.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    def _build_pending_completion_tool_call(self, pending_action: str) -> dict[str, Any] | None:
        """Parse deterministic pending-action text into an exact tool call."""
        text = str(pending_action or "").strip()
        if not text:
            return None

        document_match = re.search(
            r"Call\s+get_document\s+with\s+document_id='([^']+)'",
            text,
            flags=re.IGNORECASE,
        )
        if document_match:
            document_id = document_match.group(1).strip()
            return {
                "id": f"auto_completion_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": "get_document",
                    "arguments": json.dumps({"document_id": document_id}),
                },
            }

        event_match = re.search(
            r"Call\s+get_events\s+with\s+action='by_ids'\s+and\s+event_ids=\['([^']+)'\]",
            text,
            flags=re.IGNORECASE,
        )
        if event_match:
            event_id = event_match.group(1).strip()
            return {
                "id": f"auto_completion_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": "get_events",
                    "arguments": json.dumps({"action": "by_ids", "event_ids": [event_id]}),
                },
            }

        return None

    async def _execute_pending_completion_action(
        self,
        *,
        pending_action: str,
        state: AgentState,
        messages: list[dict[str, Any]],
        question: str,
        search_limit: int,
        run_id: str,
        user_email: Optional[str],
        conversation_history: Optional[list[dict[str, str]]],
    ) -> bool:
        """Execute deterministic pending follow-up actions directly when possible."""
        tool_call = self._build_pending_completion_tool_call(pending_action)
        if tool_call is None:
            return False

        trace.trace_decision(
            "Executing pending completion action directly",
            "Controller synthesized exact follow-up tool call",
            {"pending_action": pending_action},
        )
        await self._handle_tool_calls(
            [tool_call],
            state,
            messages,
            question,
            search_limit,
            run_id,
            user_email=user_email,
            conversation_history=conversation_history,
        )
        return True

    async def _execute_pending_completion_action_stream(
        self,
        *,
        pending_action: str,
        state: AgentState,
        messages: list[dict[str, Any]],
        question: str,
        search_limit: int,
        run_id: str,
        user_email: Optional[str],
        conversation_history: Optional[list[dict[str, str]]],
    ) -> bool:
        """Stream-safe version of deterministic pending follow-up execution."""
        tool_call = self._build_pending_completion_tool_call(pending_action)
        if tool_call is None:
            return False

        trace.trace_decision(
            "Executing pending completion action directly (stream)",
            "Controller synthesized exact follow-up tool call",
            {"pending_action": pending_action},
        )
        function = tool_call.get("function", {})
        tool_name = str(function.get("name") or "unknown")
        args = self._normalize_stream_tool_args(function.get("arguments", {}))

        messages.append({"role": "assistant", "content": "", "tool_calls": [tool_call]})
        result = await self._execute_tool_call(
            tool_call,
            state,
            question,
            search_limit,
            run_id,
            user_email=user_email,
            conversation_history=conversation_history,
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.get("id"),
                "content": json.dumps(
                    self.tool_executor.build_tool_message_payload(
                        tool_name,
                        result,
                        args=args,
                        messages=messages,
                    ),
                    ensure_ascii=False,
                    default=str,
                ),
            }
        )
        return True

    def _get_completion_evidence(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> Optional[str]:
        """Generate completion evidence string for successful tool execution."""
        return self.tool_executor.get_completion_evidence(tool_name, args, result)

    def _get_failure_guidance(
        self,
        tool_name: str,
        result: dict[str, Any],
        post_result,
    ) -> str:
        """Generate guidance message for failed tool calls."""
        return self.tool_executor.get_failure_guidance(tool_name, result, post_result)

    def _execute_handler(
        self,
        tool_name: str,
        args: dict[str, Any],
        state: AgentState,
        question: str,
        search_limit: int,
        user_email: Optional[str] = None,
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> dict[str, Any]:
        """Execute tool handler."""
        return self.tool_executor.execute_handler(
            tool_name=tool_name,
            args=args,
            state=state,
            question=question,
            search_limit=search_limit,
            user_email=user_email,
            conversation_history=conversation_history,
        )

    def _block_redundant_contact_resolution(
        self,
        state: AgentState,
        args: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Block repeated resolve_contacts calls that previously made no progress."""
        blocked_result, trace_reason = block_redundant_contact_resolution(
            state=state,
            args=args,
            normalize_tool_status=self._agent_interface().normalize_tool_status,
        )
        if blocked_result is None:
            return None
        if trace_reason:
            trace.trace_decision(
                "Blocked redundant resolve_contacts call",
                trace_reason,
                {
                    "text": sanitize_goal_text(str(args.get("text", "")).strip()),
                    "previous_status": blocked_result.get("status"),
                },
            )
        return blocked_result

    def _prepare_memory_search_arguments(
        self,
        args: dict[str, Any],
        state: AgentState,
        question: str,
        user_email: Optional[str],
        conversation_history: Optional[list[dict[str, str]]],
    ) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
        """
        Enrich search_memories args with resolved contact IDs when person mentions are present.

        Returns:
            (normalized_args, preempt_result)
            - preempt_result is set when we should ask the user for clarification instead
              of running an unfiltered memory search.
        """
        normalized_args = dict(args)
        goal_text = sanitize_goal_text(question)

        query_text = str(normalized_args.get("query") or "").strip()
        if not query_text:
            query_text = goal_text
        query_text = sanitize_goal_text(query_text)
        normalized_args["query"] = query_text

        sort_order = detect_temporal_sort_order(query_text)
        if not sort_order:
            sort_order = detect_temporal_sort_order(goal_text)
        if sort_order and not normalized_args.get("sort_order"):
            normalized_args["sort_order"] = sort_order

        temporal_now_ref = str(state.request_context.get("temporal_now_iso") or "").strip()
        if not temporal_now_ref:
            temporal_now_ref = utc_now_iso()
            state.request_context["temporal_now_iso"] = temporal_now_ref

        explicit_time_bounds = None
        if not normalized_args.get("time_start") and not normalized_args.get("time_end"):
            timezone_name = str(state.request_context.get("timezone") or "").strip() or None
            explicit_time_bounds = extract_explicit_time_bounds(
                query_text or goal_text,
                reference_time_iso=temporal_now_ref,
                timezone_name=timezone_name,
            )
            if explicit_time_bounds is not None:
                normalized_args["time_start"], normalized_args["time_end"] = explicit_time_bounds

        is_future_temporal_query = detect_future_temporal_intent(
            query_text
        ) or detect_future_temporal_intent(goal_text)
        if is_future_temporal_query and not normalized_args.get("time_start"):
            normalized_args["time_start"] = temporal_now_ref
        if (
            sort_order in {"newest", "oldest"}
            and not is_future_temporal_query
            and not normalized_args.get("time_end")
        ):
            normalized_args["time_end"] = temporal_now_ref
        if sort_order or explicit_time_bounds:
            # Temporal questions are accuracy-sensitive. Use a wider candidate window.
            current_limit = normalized_args.get("limit")
            try:
                parsed_limit = int(current_limit) if current_limit is not None else 0
            except (TypeError, ValueError):
                parsed_limit = 0
            if parsed_limit < 25:
                normalized_args["limit"] = 25

        active_scope, active_scope_ids, preempt, _ = self._ensure_contact_scope(
            state=state,
            text=query_text or goal_text,
            user_email=user_email,
            conversation_history=conversation_history,
            require_person_query=True,
        )
        if preempt is not None:
            return normalized_args, preempt

        if normalized_args.get("contact_ids"):
            normalized_args["query"] = optimize_query_for_scoped_contacts(
                query_text=query_text,
                goal_text=goal_text,
                active_scope=active_scope,
            )
            return normalized_args, None

        if active_scope_ids:
            normalized_args["contact_ids"] = list(active_scope_ids)
            normalized_args["query"] = optimize_query_for_scoped_contacts(
                query_text=query_text,
                goal_text=goal_text,
                active_scope=active_scope,
            )
            return normalized_args, None

        return normalized_args, None

    def _prepare_get_events_arguments(
        self,
        args: dict[str, Any],
        state: AgentState,
        question: str,
        user_email: Optional[str],
        conversation_history: Optional[list[dict[str, str]]],
    ) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
        """Enrich get_events args for direct temporal person-lookup questions."""
        normalized_args = dict(args)
        goal_text = sanitize_goal_text(question)

        action = GetEventsAction.from_value(normalized_args.get("action"))
        has_event_ids = bool(normalized_args.get("event_ids"))
        has_time_start = bool(str(normalized_args.get("time_start") or "").strip())
        has_time_end = bool(str(normalized_args.get("time_end") or "").strip())
        if action is None:
            if has_event_ids:
                action = GetEventsAction.BY_IDS
            elif has_time_start or has_time_end:
                action = GetEventsAction.BY_TIME_SPAN

        if action is None:
            return normalized_args, None
        normalized_args["action"] = action.value

        if action is not GetEventsAction.BY_TIME_SPAN:
            return normalized_args, None

        sort_order = detect_temporal_sort_order(goal_text)
        if sort_order and not normalized_args.get("sort_order"):
            normalized_args["sort_order"] = sort_order

        temporal_now_ref = str(state.request_context.get("temporal_now_iso") or "").strip()
        if not temporal_now_ref:
            temporal_now_ref = utc_now_iso()
            state.request_context["temporal_now_iso"] = temporal_now_ref

        if not has_time_start and not has_time_end:
            timezone_name = str(state.request_context.get("timezone") or "").strip() or None
            explicit_time_bounds = extract_explicit_time_bounds(
                goal_text,
                reference_time_iso=temporal_now_ref,
                timezone_name=timezone_name,
            )
            if explicit_time_bounds is not None:
                normalized_args["time_start"], normalized_args["time_end"] = explicit_time_bounds

        is_future_temporal_query = detect_future_temporal_intent(goal_text)
        if is_future_temporal_query and not normalized_args.get("time_start"):
            normalized_args["time_start"] = temporal_now_ref
        if (
            sort_order in {"newest", "oldest"}
            and not is_future_temporal_query
            and not normalized_args.get("time_end")
        ):
            normalized_args["time_end"] = temporal_now_ref

        active_scope, active_scope_ids, preempt, _ = self._ensure_contact_scope(
            state=state,
            text=goal_text,
            user_email=user_email,
            conversation_history=conversation_history,
            require_person_query=True,
        )
        if preempt is not None:
            return normalized_args, preempt

        if active_scope_ids and not normalized_args.get("contact_ids"):
            normalized_args["contact_ids"] = list(active_scope_ids)

        if normalized_args.get("contact_ids") and active_scope:
            optimized_query = optimize_query_for_scoped_contacts(
                query_text=goal_text,
                goal_text=goal_text,
                active_scope=active_scope,
            )
            if optimized_query == "events":
                normalized_args.pop("tags", None)
                normalized_args.pop("types", None)

        return normalized_args, None

    def _ensure_contact_scope(
        self,
        *,
        state: AgentState,
        text: str,
        user_email: Optional[str],
        conversation_history: Optional[list[dict[str, str]]],
        require_person_query: bool,
    ) -> tuple[list[dict[str, Any]], list[str], Optional[dict[str, Any]], bool]:
        """Delegate shared contact-scope reuse/resolution policy to helper module."""
        return ensure_contact_scope(
            state=state,
            text=text,
            user_email=user_email,
            conversation_history=conversation_history,
            require_person_query=require_person_query,
            normalize_tool_status=self._agent_interface().normalize_tool_status,
            update_state=self._update_contact_resolution_state,
        )

    def _block_redundant_memory_search(
        self,
        state: AgentState,
        args: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Block repeated search_memories calls that do not add new signal."""
        current_signature = self._memory_search_signature(args)
        current_limit = self._coerce_limit(args.get("limit"))

        for previous in reversed(state.tool_calls):
            if previous.tool_name != "search_memories":
                continue
            previous_signature = self._memory_search_signature(previous.arguments)
            if previous_signature != current_signature:
                continue

            previous_limit = self._coerce_limit(previous.arguments.get("limit"))
            previous_count = int((previous.result or {}).get("count", 0) or 0)
            if previous_count <= 0:
                return None
            if current_limit > previous_limit:
                return None

            return {
                **(previous.result or {}),
                "status": "no_progress",
                "message": (
                    "Equivalent memory search already executed. "
                    "Use existing results or fetch event details instead of repeating the same search."
                ),
            }

        inspected_candidate = state.get_best_information_candidate(inspected_only=True)
        if not inspected_candidate:
            return None

        candidate_id = str(inspected_candidate.get("candidate_id") or "").strip()
        candidate_kind = str(inspected_candidate.get("kind") or "").strip().lower()
        if not candidate_id:
            return None

        reference_search = self._find_latest_search_with_candidate(
            state=state,
            candidate_id=candidate_id,
            candidate_kind=candidate_kind,
        )
        if reference_search is None:
            return None

        reference_args = reference_search.arguments or {}
        reference_query = str(reference_args.get("query") or "").strip()
        current_query = str(args.get("query") or "").strip()
        overlap = self._query_overlap(reference_query, current_query)
        min_overlap = 0.65
        if overlap < min_overlap:
            return None

        same_scope = tuple(
            sorted(str(cid) for cid in (reference_args.get("contact_ids") or []))
        ) == tuple(sorted(str(cid) for cid in (args.get("contact_ids") or [])))
        if not same_scope:
            return None

        label = str(inspected_candidate.get("label") or "untitled").strip()
        return {
            **(reference_search.result or {}),
            "status": "no_progress",
            "message": (
                f"You already inspected {candidate_kind} candidate '{label}' ({candidate_id}) "
                "for this topic. Reuse that context before running another broad memory search."
            ),
        }

    def _memory_search_signature(self, args: dict[str, Any]) -> tuple[Any, ...]:
        """Build a comparable signature for memory-search de-duplication."""
        query = " ".join(str(args.get("query", "")).lower().split())
        contact_ids = tuple(sorted(str(cid) for cid in (args.get("contact_ids") or [])))
        tags = tuple(sorted(str(tag).lower() for tag in (args.get("tags") or [])))
        time_start = str(args.get("time_start") or "")
        time_end = str(args.get("time_end") or "")
        sort_order = str(args.get("sort_order") or "relevance").lower()
        return (query, contact_ids, tags, time_start, time_end, sort_order)

    def _coerce_limit(self, value: Any) -> int:
        """Parse result limit from arbitrary input with a sane default."""
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = 0
        return parsed if parsed > 0 else 5

    def _find_latest_search_with_candidate(
        self,
        state: AgentState,
        candidate_id: str,
        candidate_kind: str,
    ) -> Optional[Any]:
        """Return the latest successful search_memories call containing a candidate id."""
        target_id = str(candidate_id or "").strip()
        normalized_kind = str(candidate_kind or "").strip().lower()
        if not target_id:
            return None

        for previous in reversed(state.tool_calls):
            if previous.tool_name != "search_memories" or not previous.success:
                continue
            rows = (previous.result or {}).get("results", [])
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_id = str(row.get("id") or "").strip()
                if row_id != target_id:
                    continue
                if normalized_kind:
                    row_kind = str(row.get("kind") or "").strip().lower()
                    if row_kind and row_kind != normalized_kind:
                        continue
                return previous
        return None

    def _query_overlap(self, left: str, right: str) -> float:
        """Return token overlap ratio for two search queries."""
        left_tokens = {token for token in str(left or "").lower().split() if token}
        right_tokens = {token for token in str(right or "").lower().split() if token}
        if not left_tokens or not right_tokens:
            return 0.0
        intersection = left_tokens & right_tokens
        return len(intersection) / float(min(len(left_tokens), len(right_tokens)))

    def _prime_contact_scope_for_question(
        self,
        state: AgentState,
        question: str,
        user_email: Optional[str],
        conversation_history: Optional[list[dict[str, str]]],
    ) -> Optional[str]:
        """Resolve people from the top-level question once, before tool loop."""
        text = sanitize_goal_text(question)
        _, _, preempt, resolution_attempted = self._ensure_contact_scope(
            state=state,
            text=text,
            user_email=user_email,
            conversation_history=conversation_history,
            require_person_query=False,
        )
        if preempt is not None:
            return record_pre_resolution_outcome(state=state)

        if resolution_attempted:
            return record_pre_resolution_outcome(state=state)

        return None

    def _update_contact_resolution_state(
        self,
        state: AgentState,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Store scoped contact-resolution outcomes for subsequent tool calls."""
        apply_contact_resolution_result(
            state=state,
            args=args,
            result=result,
            normalize_tool_status=self._agent_interface().normalize_tool_status,
        )

    def _check_goal_completion(
        self,
        state: AgentState,
        final_content: str,
    ) -> dict[str, Any]:
        """
        Check if the user's goal was actually achieved.

        Uses the GoalCompletionValidator to verify that the actual request
        was completed, not just that tools were called.

        Returns:
            Dict with 'achieved', 'reason', and 'pending_actions' keys
        """
        achieved, reason, pending_actions = self.goal_validator.check_goal_achieved(
            goal=state.goal,
            tool_calls=state.tool_calls,
            known_facts=state.known_facts,
            final_content=final_content,
            intent=state.intent,
        )

        # Trace the goal check
        trace.trace_goal_check(achieved, reason, pending_actions)

        if achieved:
            state.mark_goal_achieved(reason)

        return {
            "achieved": achieved,
            "reason": reason,
            "pending_actions": pending_actions,
        }

    def _handle_limit_violation(
        self,
        state: AgentState,
        violation,
        run_id: str,
        session_id: Optional[str],
        total_start: float,
    ) -> dict[str, Any]:
        """Handle when a limit is violated."""
        message = self.limit_checker.format_stop_message(state, violation)
        duration_ms = (perf_counter() - total_start) * 1000

        # Trace limit violation
        trace.trace_limit_violation(
            violation.limit_type.value,
            violation.message,
            {
                "steps_taken": state.step_count,
                "tool_calls_made": state.tool_calls_count,
                "repairs_attempted": state.repair_count,
            },
        )

        self.logger.complete_run(
            run_id,
            success=False,
            final_answer=message,
            limit_hit=violation.limit_type.value,
        )

        trace.trace_run_complete(
            run_id,
            success=False,
            duration_ms=duration_ms,
            steps=state.step_count,
            tool_calls=state.tool_calls_count,
        )

        return {
            "question": state.goal,
            "answer": message,
            "thread_id": session_id,
            "resolution": state.resolution,
            "search_results": self._latest_search_results(state),
            "events_results": self._collected_events_results(state),
            "document_results": self._collected_document_results(state),
            "linked_items": self._build_linked_items(state, answer=""),
            "generated_files": self._build_generated_files(state),
            "ui_directives": state.ui_directives,
            "limit_hit": violation.limit_type.value,
            "profile": self.runtime_profile.name,
        }

    def _format_unable_to_complete_message(self, reason: str) -> str:
        """Build a clear user-facing failure message with an explicit reason."""
        normalized_reason = str(reason or "Agent could not finalize the response").strip()
        return f"Sorry, I couldn't complete your request. Reason: {normalized_reason}."

    def _infer_unfinalized_reason(self, state: AgentState) -> str:
        """Infer the best available reason when the run has no final answer."""
        if state.verifier_notes:
            return f"verification failed ({state.verifier_notes[-1]})"

        if state.pending_actions:
            pending = ", ".join(state.pending_actions[:2])
            return f"required actions still pending ({pending})"

        last_call = state.last_tool_call
        if last_call and not last_call.success:
            detail = str(last_call.error or "tool execution failed").strip()
            return f"last tool call `{last_call.tool_name}` failed ({detail})"

        if state.tool_calls_count > 0:
            return "agent produced no final response after tool execution"

        return "agent produced empty responses"

    def _finalize(
        self,
        question: str,
        answer: str,
        state: AgentState,
        run_id: str,
        session_id: Optional[str],
        total_start: float,
    ) -> dict[str, Any]:
        """Finalize the response."""
        # Removed: event_proposal extraction - use /event command instead
        answer = self._strip_tool_reference_artifacts(answer)

        duration_ms = (perf_counter() - total_start) * 1000

        # Validate response has actual content (clawdbot pattern)
        has_content = bool(answer and answer.strip() and len(answer.strip()) > 10)
        if not has_content and state.tool_calls_count > 0:
            # We did work but have no answer - this is suspicious
            reason = self._infer_unfinalized_reason(state)
            trace.trace_decision(
                "Empty response with tool calls",
                "Generated fallback response",
                {"tool_calls": state.tool_calls_count, "reason": reason},
            )
            answer = self._format_unable_to_complete_message(reason)

        # Log completion
        self.logger.complete_run(run_id, success=True, final_answer=answer)

        # Trace run completion with lifecycle checkpoint
        trace.trace_run_complete(
            run_id,
            success=True,
            duration_ms=duration_ms,
            steps=state.step_count,
            tool_calls=state.tool_calls_count,
            answer_preview=answer[:200] if answer else None,
        )
        trace.trace_run_lifecycle_checkpoint(
            run_id,
            phase="FINALIZED",
            steps=state.step_count,
            tool_calls=state.tool_calls_count,
            goal_achieved=state.goal_achieved,
        )

        bundle = {
            "question": question,
            "answer": answer,
            "thread_id": session_id,
            "resolution": state.resolution,
            "search_results": self._latest_search_results(state),
            "events_results": self._collected_events_results(state),
            "document_results": self._collected_document_results(state),
            "linked_items": self._build_linked_items(state, answer=answer),
            "generated_files": self._build_generated_files(state),
            "ui_directives": state.ui_directives,
            # Completion metadata (clawdbot-inspired)
            "_meta": {
                "goal_achieved": state.goal_achieved,
                "completion_evidence": state.completion_evidence,
                "steps_taken": state.step_count,
                "tool_calls_made": state.tool_calls_count,
                "duration_ms": duration_ms,
                "successful_tools": state.successful_tool_calls,
                "failed_tools": state.failed_tool_calls,
                "route_source": state.route_source,
                "route_confidence": state.route_confidence,
                "route_confidence_tier": state.route_confidence_tier,
                "tool_visibility_mode": state.tool_visibility_mode,
                "tool_visibility_escalated": state.tool_visibility_escalated,
                "tool_visibility_escalations_count": state.tool_visibility_escalations_count,
                "clarification_requests_count": state.clarification_requests_count,
                "execution_plan_steps": len(state.execution_plan),
                "execution_plan_completed_steps": len(state.completed_plan_steps),
                "verifier_notes": state.verifier_notes,
                "episodic_memory_count": len(state.episodic_memory),
                "llm_routing_profile": self._last_llm_policy.profile
                if self._last_llm_policy
                else "default",
                "llm_routing_model": self._last_llm_policy.model if self._last_llm_policy else None,
                "llm_routing_rationale": self._last_llm_policy.rationale
                if self._last_llm_policy
                else None,
                "profile": self.runtime_profile.name,
            },
        }

        if state.activated_skills:
            bundle["activated_skills"] = [s.get("name") for s in state.activated_skills]

        return bundle

    def _build_generated_files(self, state: AgentState) -> list[dict[str, Any]]:
        """Build downloadable file chips from controller-executed generation tools."""
        generated_files: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for call in state.tool_calls:
            if call.tool_name != "create_pdf" or not call.success:
                continue
            artifact = call.result.get("artifact")
            if not isinstance(artifact, dict):
                continue
            artifact_id = str(artifact.get("artifact_id") or "").strip()
            if not artifact_id or artifact_id in seen_ids:
                continue
            seen_ids.add(artifact_id)
            generated_files.append(
                {
                    "kind": "generated_pdf",
                    "artifact_id": artifact_id,
                    "title": str(artifact.get("title") or "Generated PDF").strip()
                    or "Generated PDF",
                    "filename": str(artifact.get("filename") or "generated.pdf").strip()
                    or "generated.pdf",
                    "file_mime": str(artifact.get("file_mime") or "application/pdf").strip()
                    or "application/pdf",
                    "file_size": artifact.get("file_size"),
                    "download_url": artifact.get("download_url"),
                    "web_download_url": artifact.get("web_download_url"),
                    "mobile_download_url": artifact.get("mobile_download_url"),
                }
            )
        return generated_files

    def _strip_tool_reference_artifacts(self, answer: str) -> str:
        """Remove bracketed tool-field placeholders accidentally surfaced by the model."""
        text = str(answer or "")
        if not text:
            return text

        cleaned = re.sub(r"[\[【]\s*[A-Za-z_-]+:[A-Za-z0-9_\-]+\s*[\]】]", "", text)
        cleaned = re.sub(r"[^\S\r\n]+([,.;:!?])", r"\1", cleaned)
        cleaned = re.sub(r"[^\S\r\n]{2,}", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _build_linked_items(
        self,
        state: AgentState,
        answer: str = "",
        max_items: int = 5,
    ) -> list[dict[str, Any]]:
        """Build ranked deep links from answer-aligned entity candidates."""
        candidates = self._build_linked_item_candidates(state, answer=answer)
        if not candidates:
            return []

        selected: list[dict[str, Any]] = []
        selected_candidates: list[dict[str, Any]] = []
        selected_keys: set[tuple[str, str]] = set()
        role_order = ["primary_answer_anchor", "subject_entity", "context_anchor", "evidence_anchor"]
        question_text = str(state.goal or "")
        narrow_question = self._linked_item_is_narrow_question(question_text)
        needs_context_role = self._linked_item_needs_context_role(question_text)
        query_signals = self._linked_item_query_signals(normalize_search_text(question_text))

        for role in role_order:
            role_candidates = [
                candidate
                for candidate in candidates
                if candidate.get("selected_role") == role
                and candidate.get("role_score", 0.0) >= 1.05
                and (candidate["entity_type"], candidate["entity_id"]) not in selected_keys
            ]
            if not role_candidates:
                continue
            picked = role_candidates[0]
            selected.append(self._linked_item_payload(picked))
            selected_candidates.append(picked)
            selected_keys.add((picked["entity_type"], picked["entity_id"]))
            if len(selected) >= max_items:
                return selected
            if narrow_question and not needs_context_role:
                selected_roles = {str(item.get("role") or "") for item in selected}
                selected_types = {str(item.get("entity_type") or "") for item in selected}
                if (
                    "primary_answer_anchor" in selected_roles and "subject_entity" in selected_roles
                ) or ("event" in selected_types and "contact" in selected_types):
                    return selected

        if narrow_question and not needs_context_role and len(selected) >= 2:
            return selected

        if query_signals["where"]:
            promoted_place = self._linked_item_place_for_selected_event(
                selected_candidates=selected_candidates,
                candidates=candidates,
                selected_keys=selected_keys,
            )
            if promoted_place is not None:
                selected.append(self._linked_item_payload(promoted_place))
                selected_candidates.append(promoted_place)
                selected_keys.add((promoted_place["entity_type"], promoted_place["entity_id"]))
                if len(selected) >= max_items:
                    return selected

        for candidate in candidates:
            dedupe_key = (candidate["entity_type"], candidate["entity_id"])
            if dedupe_key in selected_keys:
                continue
            if candidate.get("role_score", 0.0) < 1.05:
                continue
            if candidate.get("overall_score", 0.0) < 0.9 or candidate.get("support_score", 0.0) < 0.55:
                continue
            if candidate.get("selected_role") == "evidence_anchor" and candidate.get("entity_type") == "event":
                continue
            selected.append(self._linked_item_payload(candidate))
            selected_candidates.append(candidate)
            selected_keys.add(dedupe_key)
            if len(selected) >= max_items:
                break

        return selected

    def _build_linked_item_candidates(
        self,
        state: AgentState,
        *,
        answer: str,
    ) -> list[dict[str, Any]]:
        question_text = str(state.goal or "").strip()
        normalized_question = normalize_search_text(question_text)
        normalized_answer = normalize_search_text(answer or "")
        question_tokens = self._linked_item_tokens(normalized_question)
        answer_tokens = self._linked_item_tokens(normalized_answer)
        query_signals = self._linked_item_query_signals(normalized_question)
        event_map = {
            str(event.get("id") or event.get("event_id") or "").strip(): event
            for event in self._collected_events_results(state)
            if isinstance(event, dict) and str(event.get("id") or event.get("event_id") or "").strip()
        }
        document_map = {
            str(document.get("document_id") or "").strip(): document
            for document in self._collected_document_results(state)
            if isinstance(document, dict) and str(document.get("document_id") or "").strip()
        }

        ranked: list[dict[str, Any]] = []
        candidate_pool = [
            *self._linked_item_resolution_candidates(state, question_text=question_text),
            *state.information_candidates,
        ]
        for candidate in candidate_pool:
            if not isinstance(candidate, dict):
                continue
            entity_type = str(candidate.get("kind") or "").strip().lower()
            entity_id = str(candidate.get("candidate_id") or "").strip()
            if entity_type not in {"event", "document", "contact", "place"} or not entity_id:
                continue

            payload = self._linked_item_entity_payload(
                entity_type=entity_type,
                entity_id=entity_id,
                candidate=candidate,
                event_map=event_map,
                document_map=document_map,
            )
            if payload is None:
                continue

            scores, support_score = self._score_linked_item_candidate(
                payload,
                candidate,
                normalized_question=normalized_question,
                normalized_answer=normalized_answer,
                question_tokens=question_tokens,
                answer_tokens=answer_tokens,
                query_signals=query_signals,
            )
            selected_role, role_score = max(scores.items(), key=lambda item: item[1])
            ranked.append(
                {
                    **payload,
                    "scores": scores,
                    "selected_role": selected_role,
                    "role_score": role_score,
                    "overall_score": max(scores.values()),
                    "candidate": candidate,
                    "support_score": support_score,
                }
            )

        ranked.sort(
            key=lambda item: (
                float(item.get("overall_score", 0.0) or 0.0),
                float(item.get("role_score", 0.0) or 0.0),
                float(item.get("support_score", 0.0) or 0.0),
                1 if item.get("selected_role") == "primary_answer_anchor" else 0,
                1 if item.get("inspected") else 0,
                int(item.get("times_seen", 0) or 0),
            ),
            reverse=True,
        )
        return ranked

    def _linked_item_place_for_selected_event(
        self,
        *,
        selected_candidates: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        selected_keys: set[tuple[str, str]],
    ) -> dict[str, Any] | None:
        primary_event = next(
            (
                candidate
                for candidate in selected_candidates
                if str(candidate.get("entity_type") or "") == "event"
            ),
            None,
        )
        if primary_event is None:
            return None
        metadata = primary_event.get("metadata") if isinstance(primary_event.get("metadata"), dict) else {}
        place_id = str(metadata.get("place_id") or "").strip()
        if not place_id:
            return None
        for candidate in candidates:
            if (
                str(candidate.get("entity_type") or "") == "place"
                and str(candidate.get("entity_id") or "") == place_id
                and ("place", place_id) not in selected_keys
            ):
                boosted = dict(candidate)
                boosted["selected_role"] = "context_anchor"
                boosted["role_score"] = max(float(candidate.get("role_score", 0.0) or 0.0), 1.35)
                boosted["overall_score"] = max(float(candidate.get("overall_score", 0.0) or 0.0), boosted["role_score"])
                return boosted
        return None

    def _linked_item_resolution_candidates(
        self,
        state: AgentState,
        *,
        question_text: str,
    ) -> list[dict[str, Any]]:
        active_scope = state.resolution.get("active_contact_scope") or []
        if not isinstance(active_scope, list):
            return []

        synthetic: list[dict[str, Any]] = []
        seen_contact_ids: set[str] = set()
        for item in active_scope:
            if not isinstance(item, dict):
                continue
            contact_id = str(item.get("contact_id") or "").strip()
            if not contact_id or contact_id in seen_contact_ids:
                continue
            mention_text = str(item.get("mention_text") or "").strip()
            display_name = str(item.get("display_name") or "").strip() or mention_text or contact_id
            if mention_text.strip().lower() == "user":
                continue
            seen_contact_ids.add(contact_id)
            synthetic.append(
                {
                    "kind": "contact",
                    "candidate_id": contact_id,
                    "label": display_name,
                    "best_score": 1.4,
                    "times_seen": 1,
                    "last_query": question_text,
                    "last_source_tool": "pre_resolved_contacts",
                    "last_seen_step": state.step_count,
                    "inspected": False,
                    "inspected_step": None,
                    "role_hints": ["subject_entity", "context_anchor"],
                    "metadata": {
                        "display_name": display_name,
                        "resolution_text": mention_text or display_name,
                        "matched_via": item.get("matched_via"),
                        "confidence": item.get("confidence"),
                        "controller_scope": True,
                    },
                }
            )
        return synthetic

    def _linked_item_entity_payload(
        self,
        *,
        entity_type: str,
        entity_id: str,
        candidate: dict[str, Any],
        event_map: dict[str, dict[str, Any]],
        document_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
        label = str(candidate.get("label") or "").strip() or entity_id
        base_payload: dict[str, Any] = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "title": label,
            "subtitle": None,
            "inspected": bool(candidate.get("inspected")),
            "times_seen": int(candidate.get("times_seen", 0) or 0),
            "source_tool": str(candidate.get("last_source_tool") or "").strip(),
            "role_hints": list(candidate.get("role_hints") or []),
            "metadata": metadata,
            "best_score": candidate.get("best_score"),
        }

        if entity_type == "event":
            event = event_map.get(entity_id, {})
            title = str(event.get("title") or label).strip() or label
            subtitle = str(event.get("start_date") or event.get("end_date") or metadata.get("start_date") or "").strip() or None
            searchable_parts = [title, subtitle or "", str(event.get("summary") or ""), " ".join(event.get("tags") or []), " ".join(event.get("types") or [])]
            place = event.get("place") if isinstance(event.get("place"), dict) else None
            if place:
                searchable_parts.extend([str(place.get("name") or ""), str(place.get("city") or "")])
            return {
                **base_payload,
                "title": title,
                "subtitle": subtitle,
                "searchable_text": " ".join(part for part in searchable_parts if part),
            }

        if entity_type == "document":
            document = document_map.get(entity_id, {})
            title = str(document.get("title") or label).strip() or label
            subtitle = str(document.get("file_name") or metadata.get("file_name") or "").strip() or None
            searchable_parts = [title, subtitle or "", str(document.get("snippet") or metadata.get("snippet") or "")]
            searchable_parts.extend(document.get("tags") or metadata.get("tags") or [])
            return {
                **base_payload,
                "title": title,
                "subtitle": subtitle,
                "searchable_text": " ".join(str(part) for part in searchable_parts if part),
            }

        if entity_type == "contact":
            title = str(metadata.get("display_name") or label).strip() or label
            subtitle = None
            email_values = metadata.get("emails") if isinstance(metadata.get("emails"), list) else []
            if email_values:
                subtitle = str(email_values[0]).strip() or None
            searchable_parts = [title, subtitle or ""]
            searchable_parts.extend(metadata.get("aliases") if isinstance(metadata.get("aliases"), list) else [])
            return {
                **base_payload,
                "title": title,
                "subtitle": subtitle,
                "searchable_text": " ".join(str(part) for part in searchable_parts if part),
            }

        if entity_type == "place":
            title = str(metadata.get("name") or label).strip() or label
            subtitle_parts = [
                str(metadata.get("city") or "").strip(),
                str(metadata.get("country") or "").strip(),
            ]
            subtitle = ", ".join(part for part in subtitle_parts if part) or None
            searchable_parts = [title, subtitle or "", str(metadata.get("address") or "")]
            searchable_parts.extend(metadata.get("roles") if isinstance(metadata.get("roles"), list) else [])
            return {
                **base_payload,
                "title": title,
                "subtitle": subtitle,
                "searchable_text": " ".join(str(part) for part in searchable_parts if part),
            }

        return None

    def _score_linked_item_candidate(
        self,
        payload: dict[str, Any],
        candidate: dict[str, Any],
        *,
        normalized_question: str,
        normalized_answer: str,
        question_tokens: set[str],
        answer_tokens: set[str],
        query_signals: dict[str, bool],
    ) -> tuple[dict[str, float], float]:
        searchable_text = normalize_search_text(str(payload.get("searchable_text") or payload.get("title") or ""))
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        label = normalize_search_text(str(payload.get("title") or ""))
        role_hints = {str(role).strip().lower() for role in (payload.get("role_hints") or []) if str(role).strip()}
        source_tool = str(payload.get("source_tool") or "").strip().lower()
        inspected = bool(payload.get("inspected"))

        overlap = self._linked_item_overlap_score(question_tokens, searchable_text)
        answer_overlap = self._linked_item_overlap_score(answer_tokens, searchable_text)
        label_in_question = 1.0 if label and label in normalized_question else 0.0
        label_in_answer = 1.0 if label and label in normalized_answer else 0.0
        query_match = 0.0
        last_query = normalize_search_text(str(candidate.get("last_query") or ""))
        if last_query:
            if last_query == normalized_question:
                query_match = 1.0
            elif last_query in normalized_question or normalized_question in last_query:
                query_match = 0.7

        try:
            best_score = max(0.0, float(payload.get("best_score"))) if payload.get("best_score") is not None else 0.0
        except (TypeError, ValueError):
            best_score = 0.0
        retrieval_score = min(1.2, best_score)
        inspection_score = 0.25 if inspected else 0.0
        direct_lookup_score = 0.0
        if source_tool in {"lookup_contact", "lookup_places", "lookup_contact_places", "lookup_place_contacts", "resolve_contacts", "pre_resolved_contacts"}:
            direct_lookup_score = 0.2
        elif source_tool in {"get_events", "get_document"}:
            direct_lookup_score = 0.12

        primary = overlap + (answer_overlap * 0.35) + (label_in_answer * 0.65) + query_match + retrieval_score + inspection_score + direct_lookup_score
        subject = overlap + (label_in_question * 0.75) + (query_match * 0.8) + (0.6 if "subject_entity" in role_hints else 0.0) + (0.4 if source_tool in {"resolve_contacts", "lookup_contact", "lookup_place_contacts"} else 0.0)
        context = (overlap * 0.8) + (label_in_question * 0.35) + (0.55 if "context_anchor" in role_hints else 0.0) + (0.3 if source_tool in {"lookup_places", "lookup_contact_places", "lookup_place_contacts"} else 0.0)
        evidence = (overlap * 0.45) + (answer_overlap * 0.55) + (label_in_answer * 0.45) + (0.25 if "evidence_anchor" in role_hints else 0.0) + (0.25 if inspected else 0.0)

        entity_type = str(payload.get("entity_type") or "")
        if entity_type == "event":
            if query_signals["temporal"] or query_signals["interaction"]:
                primary += 0.55
            if query_signals["where"]:
                primary += 0.2
            if metadata.get("place_id") and query_signals["where"]:
                context += 0.15
        elif entity_type == "document":
            if query_signals["document_like"]:
                primary += 0.55
            if query_signals["what"]:
                evidence += 0.15
        elif entity_type == "contact":
            if query_signals["who"] or query_signals["person_like"] or query_signals["interaction"]:
                primary += 0.2
                subject += 0.75
            if metadata.get("resolution_text"):
                subject += 0.25
            if metadata.get("controller_scope"):
                subject += 1.35
        elif entity_type == "place":
            if query_signals["where"] or query_signals["place_like"]:
                primary += 0.45
                context += 0.35

        if label_in_answer:
            primary += 0.35
            subject += 0.15
            context += 0.15
        if label_in_question:
            subject += 0.2
            context += 0.1

        support_score = max(overlap, answer_overlap, label_in_question, label_in_answer, query_match)
        return ({
            "primary_answer_anchor": round(primary, 4),
            "subject_entity": round(subject, 4),
            "context_anchor": round(context, 4),
            "evidence_anchor": round(evidence, 4),
        }, round(support_score, 4))

    def _linked_item_payload(self, candidate: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_type": candidate.get("entity_type"),
            "entity_id": candidate.get("entity_id"),
            "title": candidate.get("title"),
            "subtitle": candidate.get("subtitle"),
            "role": candidate.get("selected_role"),
        }

    def _linked_item_tokens(self, text: str) -> set[str]:
        stop_words = {
            "the", "and", "with", "from", "that", "this", "what", "when", "where", "which", "were", "have", "about", "into", "your", "their", "them", "they", "been", "last",
        }
        tokens: set[str] = set()
        for token in str(text or "").replace("/", " ").replace("-", " ").split():
            cleaned = "".join(ch for ch in token if ch.isalnum())
            if len(cleaned) < 3 or cleaned in stop_words:
                continue
            tokens.add(cleaned)
        return tokens

    def _linked_item_overlap_score(self, tokens: set[str], searchable_text: str) -> float:
        if not tokens or not searchable_text:
            return 0.0
        matched = sum(1 for token in tokens if token in searchable_text)
        return min(1.25, matched * 0.28)

    def _linked_item_is_narrow_question(self, question_text: str) -> bool:
        normalized = normalize_search_text(question_text)
        if not normalized:
            return False
        tokens = set(normalized.split())
        if {"all", "every", "list", "show", "summarize", "summary", "recap", "report"} & tokens:
            return False
        return bool({"when", "where", "who", "last", "latest", "first"} & tokens)

    def _linked_item_needs_context_role(self, question_text: str) -> bool:
        normalized = normalize_search_text(question_text)
        if not normalized:
            return False
        return any(marker in normalized for marker in [" of ", " at ", " from ", " my ", " your ", " their "])

    def _linked_item_query_signals(self, normalized_question: str) -> dict[str, bool]:
        text = normalized_question or ""
        tokens = set(text.split())
        return {
            "where": any(phrase in text for phrase in ["where ", "where did", "where was", "where is", "where are"]),
            "when": any(phrase in text for phrase in ["when ", "when did", "when was", "when is"]),
            "who": any(phrase in text for phrase in ["who ", "who did", "who is", "who was"]),
            "what": any(phrase in text for phrase in ["what ", "what is", "what was", "which "]),
            "temporal": bool({"last", "latest", "recent", "newest", "oldest", "first"} & tokens),
            "interaction": bool({"meet", "met", "meeting", "talk", "talked", "spoke", "call", "called", "conversation", "visit", "visited"} & tokens),
            "document_like": bool({"document", "documents", "doc", "report", "file", "files", "note", "notes", "lab", "result", "results"} & tokens),
            "person_like": bool({"person", "people", "owner", "friend", "wife", "husband", "daughter", "son", "boss", "manager", "doctor"} & tokens),
            "place_like": bool({"place", "home", "house", "office", "school", "address", "there", "here", "location"} & tokens),
        }

    def _latest_search_results(self, state: AgentState) -> list[dict[str, Any]]:
        """Return the latest successful search_memories result rows."""
        for call in reversed(state.tool_calls):
            if call.tool_name != "search_memories" or not call.success:
                continue
            rows = (call.result or {}).get("results", [])
            if isinstance(rows, list):
                return rows
        return []

    def _collected_events_results(self, state: AgentState) -> list[dict[str, Any]]:
        """Collect event payloads from successful get_events calls."""
        collected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for call in state.tool_calls:
            if call.tool_name not in {"get_events", "summarize_memories"} or not call.success:
                continue
            events = (call.result or {}).get("events", [])
            if not isinstance(events, list):
                continue
            for event in events:
                if not isinstance(event, dict):
                    continue
                event_id = str(event.get("id") or event.get("event_id") or "").strip()
                dedupe_key = event_id or json.dumps(event, sort_keys=True, default=str)
                if dedupe_key in seen_ids:
                    continue
                seen_ids.add(dedupe_key)
                collected.append(event)
        return collected

    def _collected_document_results(self, state: AgentState) -> list[dict[str, Any]]:
        """Collect compact document payloads from successful get_document calls."""
        collected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for call in state.tool_calls:
            if not call.success:
                continue
            if call.tool_name == "get_document":
                document = (call.result or {}).get("document")
                if not isinstance(document, dict):
                    continue
                compact = self._compact_document_result(document)
                document_id = str(compact.get("document_id") or "").strip()
                dedupe_key = document_id or json.dumps(compact, sort_keys=True, default=str)
                if dedupe_key in seen_ids:
                    continue
                seen_ids.add(dedupe_key)
                collected.append(compact)
                continue
            if call.tool_name != "summarize_memories":
                continue
            documents = (call.result or {}).get("inspected_documents") or (call.result or {}).get(
                "documents"
            )
            if not isinstance(documents, list):
                continue
            for document in documents:
                if not isinstance(document, dict):
                    continue
                compact = {
                    "document_id": document.get("document_id") or document.get("id"),
                    "title": document.get("title"),
                    "tags": document.get("tags"),
                    "document_date": document.get("document_date"),
                    "file_name": document.get("file_name"),
                    "file_mime": document.get("file_mime"),
                    "file_size": document.get("file_size"),
                    "snippet": document.get("snippet"),
                }
                document_id = str(compact.get("document_id") or "").strip()
                dedupe_key = document_id or json.dumps(compact, sort_keys=True, default=str)
                if dedupe_key in seen_ids:
                    continue
                seen_ids.add(dedupe_key)
                collected.append(compact)
        return collected

    def _compact_document_result(self, document: dict[str, Any]) -> dict[str, Any]:
        """Build a compact document result for response bundles."""
        compact: dict[str, Any] = {
            "document_id": document.get("document_id"),
            "title": document.get("title"),
            "tags": document.get("tags"),
            "document_date": document.get("document_date"),
            "file_name": document.get("file_name"),
            "file_mime": document.get("file_mime"),
            "file_size": document.get("file_size"),
            "snippet": document.get("snippet"),
        }
        return compact

    def _extract_event_proposal(self, content: str) -> Optional[dict[str, Any]]:
        """Extract event proposal from content."""
        start = "<event_proposal>"
        end = "</event_proposal>"

        start_idx = content.find(start)
        if start_idx == -1:
            return None

        end_idx = content.find(end, start_idx)
        if end_idx == -1:
            return None

        try:
            json_str = content[start_idx + len(start) : end_idx].strip()
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None

    def _strip_event_proposal(self, content: str) -> str:
        """Remove event proposal XML from content."""
        start = "<event_proposal>"
        end = "</event_proposal>"

        start_idx = content.find(start)
        if start_idx == -1:
            return content

        end_idx = content.find(end, start_idx)
        if end_idx == -1:
            return content

        before = content[:start_idx].rstrip()
        after = content[end_idx + len(end) :].lstrip()

        return (before + " " + after).strip() if after else before
