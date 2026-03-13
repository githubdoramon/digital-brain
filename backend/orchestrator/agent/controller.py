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

import json
import os

# Import with absolute paths to avoid circular imports
import sys
from collections.abc import AsyncGenerator
from contextvars import ContextVar
from time import perf_counter
from typing import Any, Optional

from agent.agent_interfaces import (
    ConversationalAgentInterface,
    build_default_conversational_interface,
)
from agents.registry import build_conversational_profile_registry, choose_profile_interface
from location_inference import infer_current_place
from observability import trace
from observability.logger import get_runtime_logger
from ui_dsl.clarification import extract_need_user_input
from ui_dsl.command_adapters import command_result_to_ui_directives

from .contact_resolution import (
    build_contact_clarification_result,
    get_user_clarification_prompt_for_contact_resolution,
    is_contact_referential_memory_query,
    resolve_contacts_for_text,
    should_pre_resolve_contacts,
)
from .enums import (
    ConfidenceTier,
    FollowUpSource,
    LimitAction,
    ToolStatus,
    ToolVisibilityMode,
)
from .guardrails import (
    detect_future_temporal_intent,
    detect_temporal_sort_order,
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
        self.llm_model = os.getenv("LLM_CHAT_MODEL", "")
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
        run_id: str,
    ) -> tuple[LimitAction, list[dict[str, Any]], Any | None, str | None]:
        """Run hard/no-progress checks and attempt recovery escalation when possible."""
        hard_violation = self.limit_checker.check(state)
        if hard_violation:
            return LimitAction.VIOLATION, tools, hard_violation, None

        no_progress_violation = self.limit_checker.detect_no_progress(state)
        if no_progress_violation:
            if self._should_escalate_tool_visibility(state, no_progress_violation):
                escalated_tools = self._escalate_tool_visibility(
                    run_id=run_id,
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
    ) -> AgentState:
        """Build the canonical AgentState shared by sync/stream paths."""
        state = AgentState(goal=question)
        state.request_context = self._normalize_client_context(client_context)
        normalized_submission = self._normalize_ui_submission(ui_submission)
        if normalized_submission:
            state.request_context["ui_submission"] = normalized_submission
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
            run_id=run_id,
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
                tool_calls = message.get("tool_calls") or []

                # Trace LLM response
                trace.trace_llm_response(
                    llm_duration,
                    has_tool_calls=bool(tool_calls),
                    tool_count=len(tool_calls),
                    content_preview=content if not tool_calls else None,
                )
                self.logger.log_llm_call(
                    run_id,
                    state.step_count,
                    llm_duration,
                    content=content,
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
                            run_id=run_id,
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
                )
                self.logger.log_llm_call(
                    run_id,
                    state.step_count,
                    llm_duration,
                    content=final_content_preview,
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

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.get("id"),
                                "content": json.dumps(
                                    self.tool_executor.build_tool_message_payload(
                                        func_name,
                                        result,
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
                            run_id=run_id,
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
        run_id: str,
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
                        try:
                            normalized_location["accuracy_m"] = round(float(str(accuracy)), 1)
                        except (TypeError, ValueError):
                            pass

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
        return call_llm_with_tools(
            messages,
            tools,
            model=model,
            timeout=timeout,
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
        async for chunk in stream_llm_with_tools(
            messages,
            tools,
            model=model,
            timeout=timeout,
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
        text = sanitize_goal_text(str(args.get("text", "")).strip())
        if not text:
            return None

        scoped_text = sanitize_goal_text(
            str(state.resolution.get("active_contact_scope_text", "")).strip()
        )
        scoped_ids = state.resolution.get("active_contact_scope_ids", [])
        if scoped_text and scoped_text.lower() == text.lower() and scoped_ids:
            cached_result = state.resolution.get("contact_resolution") or {}
            return {
                **cached_result,
                "status": ToolStatus.SUCCESS.value,
                "message": "Contact scope is already resolved for this request.",
            }

        pending_text = sanitize_goal_text(
            str(state.resolution.get("pending_contact_scope_text", "")).strip()
        )
        pending_need_user_input = state.resolution.get("pending_contact_need_user_input")
        pending_prompt = ""
        if isinstance(pending_need_user_input, dict):
            pending_prompt = str(pending_need_user_input.get("prompt") or "").strip()
        if pending_prompt and pending_text and pending_text.lower() == text.lower():
            return {
                "status": ToolStatus.NEED_USER_INPUT.value,
                "ambiguous_contacts": state.resolution.get(
                    "pending_contact_ambiguous_contacts", []
                ),
                "people_mentioned": state.resolution.get("pending_contact_people", []),
                "message": "Contact resolution already requires clarification.",
                "need_user_input": pending_need_user_input,
            }

        last_call = state.last_tool_call
        if not last_call or last_call.tool_name != "resolve_contacts":
            return None

        last_text = sanitize_goal_text(str(last_call.arguments.get("text", "")).strip())
        last_result = last_call.result or {}
        last_status = self._agent_interface().normalize_tool_status(
            last_result,
            "resolve_contacts",
        )
        if last_text.lower() != text.lower():
            return None
        if last_status not in {ToolStatus.NEED_USER_INPUT, ToolStatus.NO_PEOPLE}:
            return None

        reason = (
            "Contact resolution already returned ambiguity for this exact text. "
            "Ask the user to clarify instead of retrying the same call."
            if last_status is ToolStatus.NEED_USER_INPUT
            else "No people were detected for this text in the previous attempt."
        )
        trace.trace_decision(
            "Blocked redundant resolve_contacts call",
            reason,
            {"text": text, "previous_status": last_status},
        )
        return {
            **last_result,
            "status": last_status.value,
            "message": reason,
        }

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
        if sort_order:
            # Temporal questions are accuracy-sensitive. Use a wider candidate window.
            current_limit = normalized_args.get("limit")
            try:
                parsed_limit = int(current_limit) if current_limit is not None else 0
            except (TypeError, ValueError):
                parsed_limit = 0
            if parsed_limit < 25:
                normalized_args["limit"] = 25

        pending_need_user_input = state.resolution.get("pending_contact_need_user_input")
        pending_prompt = ""
        if isinstance(pending_need_user_input, dict):
            pending_prompt = str(pending_need_user_input.get("prompt") or "").strip()
        if pending_prompt:
            preempt = build_contact_clarification_result(
                ambiguous_contacts=state.resolution.get("pending_contact_ambiguous_contacts", []),
                people_mentioned=state.resolution.get("pending_contact_people", []),
            )
            return normalized_args, preempt

        active_scope = state.resolution.get("active_contact_scope") or []

        if normalized_args.get("contact_ids"):
            normalized_args["query"] = optimize_query_for_scoped_contacts(
                query_text=query_text,
                goal_text=goal_text,
                active_scope=active_scope,
            )
            return normalized_args, None

        active_scope_ids = state.resolution.get("active_contact_scope_ids")
        if active_scope_ids:
            normalized_args["contact_ids"] = list(active_scope_ids)
            normalized_args["query"] = optimize_query_for_scoped_contacts(
                query_text=query_text,
                goal_text=goal_text,
                active_scope=active_scope,
            )
            return normalized_args, None

        if user_email and is_contact_referential_memory_query(query_text, goal_text):
            resolution = resolve_contacts_for_text(
                state=state,
                text=query_text or goal_text,
                user_email=user_email,
                conversation_history=conversation_history,
                update_state=self._update_contact_resolution_state,
            )

            if resolution:
                status = self._agent_interface().normalize_tool_status(
                    resolution,
                    "resolve_contacts",
                )
                if status is ToolStatus.NEED_USER_INPUT:
                    preempt = build_contact_clarification_result(
                        ambiguous_contacts=state.resolution.get(
                            "pending_contact_ambiguous_contacts", []
                        ),
                        people_mentioned=state.resolution.get("pending_contact_people", []),
                    )
                    return normalized_args, preempt

                if status is ToolStatus.SUCCESS:
                    active_scope = state.resolution.get("active_contact_scope") or []
                    active_scope_ids = state.resolution.get("active_contact_scope_ids") or []
                    if active_scope_ids:
                        normalized_args["contact_ids"] = list(active_scope_ids)
                        normalized_args["query"] = optimize_query_for_scoped_contacts(
                            query_text=query_text,
                            goal_text=goal_text,
                            active_scope=active_scope,
                        )
                        return normalized_args, None

        return normalized_args, None

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
        if not user_email:
            return None

        if state.resolution.get("pending_contact_need_user_input"):
            return get_user_clarification_prompt_for_contact_resolution(state)
        if state.resolution.get("active_contact_scope_ids"):
            return None

        text = sanitize_goal_text(question)
        if not text:
            return None

        resolution = resolve_contacts_for_text(
            state=state,
            text=text,
            user_email=user_email,
            conversation_history=conversation_history,
            update_state=self._update_contact_resolution_state,
        )
        if not resolution:
            return None

        status = self._agent_interface().normalize_tool_status(
            resolution,
            "resolve_contacts",
        )
        if status is ToolStatus.SUCCESS:
            scope_ids = state.resolution.get("active_contact_scope_ids", [])
            if scope_ids:
                state.add_fact(f"Pre-resolved {len(scope_ids)} contact(s) from user question")
            else:
                # Resolution ran but found no existing contacts — record this
                # so the agent loop does not redundantly call resolve_contacts.
                new_contacts = resolution.get("new_contacts", [])
                people = resolution.get("people_mentioned", [])
                state.resolution["pre_resolution_attempted"] = True
                state.resolution["pre_resolution_people"] = people
                state.resolution["pre_resolution_new_contacts"] = [
                    str(c.get("display_name") or c.get("original_text", ""))
                    for c in new_contacts
                    if isinstance(c, dict)
                ]
                if people:
                    state.add_fact(
                        f"Pre-resolved contacts for {people}: no existing contacts found"
                    )
        elif status is ToolStatus.NEED_USER_INPUT:
            prompt = get_user_clarification_prompt_for_contact_resolution(state)
            if prompt:
                state.add_question(prompt)
                return prompt

        return None

    def _update_contact_resolution_state(
        self,
        state: AgentState,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Store scoped contact-resolution outcomes for subsequent tool calls."""
        need_user_input = extract_need_user_input(
            result,
            default_source="resolve_contacts",
        )
        status = self._agent_interface().normalize_tool_status(
            result,
            "resolve_contacts",
        )

        state.resolution["last_contact_resolution_text"] = args.get("text", "")
        state.resolution["last_contact_resolution_status"] = status.value
        if status is ToolStatus.SUCCESS:
            resolved_contacts = result.get("resolved_contacts", [])
            contact_ids = [
                c.get("contact_id")
                for c in resolved_contacts
                if isinstance(c, dict) and c.get("contact_id")
            ]
            deduped_ids = list(dict.fromkeys(contact_ids))
            if deduped_ids:
                scope_entries: list[dict[str, Any]] = []
                seen_scope_ids: set[str] = set()
                for item in resolved_contacts:
                    if not isinstance(item, dict):
                        continue
                    contact_id = str(item.get("contact_id") or "").strip()
                    if not contact_id or contact_id in seen_scope_ids:
                        continue
                    seen_scope_ids.add(contact_id)
                    scope_entries.append(
                        {
                            "mention_text": str(item.get("original_text") or "").strip(),
                            "display_name": str(item.get("display_name") or "").strip(),
                            "contact_id": contact_id,
                            "confidence": item.get("confidence"),
                            "matched_via": item.get("matched_via"),
                        }
                    )
                state.resolution["active_contact_scope_ids"] = deduped_ids
                state.resolution["active_contact_scope"] = scope_entries
                state.resolution["active_contact_scope_text"] = args.get("text", "")
                state.resolution.pop("pending_contact_need_user_input", None)
                state.resolution.pop("pending_contact_ambiguous_contacts", None)
                state.resolution.pop("pending_contact_people", None)
                state.resolution.pop("pending_contact_scope_text", None)
                if state.ui_directives:
                    state.ui_directives = None
            else:
                state.resolution.pop("active_contact_scope", None)
            return

        if status is ToolStatus.NEED_USER_INPUT:
            ambiguous_contacts = result.get("ambiguous_contacts", [])
            if not need_user_input:
                fallback = build_contact_clarification_result(
                    ambiguous_contacts=ambiguous_contacts,
                    people_mentioned=result.get("people_mentioned", []),
                )
                need_user_input = extract_need_user_input(
                    fallback,
                    default_source="resolve_contacts",
                )
            if not need_user_input:
                prompt = "I found multiple matching people. Please clarify which one you mean."
                need_user_input = {
                    "kind": "disambiguation",
                    "prompt": prompt,
                    "questions": [prompt],
                    "submission_mode": "ui_submission",
                }
            state.resolution["pending_contact_need_user_input"] = need_user_input
            state.resolution["pending_contact_ambiguous_contacts"] = ambiguous_contacts
            state.resolution["pending_contact_people"] = result.get("people_mentioned", [])
            state.resolution["pending_contact_scope_text"] = args.get("text", "")
            state.resolution.pop("active_contact_scope_ids", None)
            state.resolution.pop("active_contact_scope_text", None)
            state.resolution.pop("active_contact_scope", None)

            directive = command_result_to_ui_directives(
                {
                    "type": "need_user_input",
                    "need_user_input": state.resolution.get("pending_contact_need_user_input"),
                }
            )
            if directive:
                state.ui_directives = directive
            return

        if status is ToolStatus.NO_PEOPLE:
            # No person context found; clear scoped contact state.
            state.resolution.pop("active_contact_scope_ids", None)
            state.resolution.pop("active_contact_scope_text", None)
            state.resolution.pop("active_contact_scope", None)
            state.resolution.pop("pending_contact_need_user_input", None)
            state.resolution.pop("pending_contact_ambiguous_contacts", None)
            state.resolution.pop("pending_contact_people", None)
            state.resolution.pop("pending_contact_scope_text", None)
            if state.ui_directives:
                state.ui_directives = None

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
            "linked_items": self._build_linked_items(state),
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
            "linked_items": self._build_linked_items(state),
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

    def _build_linked_items(self, state: AgentState, max_items: int = 5) -> list[dict[str, Any]]:
        """Build bounded, deterministic deep-link candidates from inspected tool results."""
        items: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for event in self._collected_events_results(state):
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or event.get("event_id") or "").strip()
            if not event_id:
                continue
            dedupe_key = ("event", event_id)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            title = str(event.get("title") or "").strip() or f"Event {event_id}"
            subtitle = str(event.get("start_date") or event.get("end_date") or "").strip() or None
            items.append(
                {
                    "entity_type": "event",
                    "entity_id": event_id,
                    "title": title,
                    "subtitle": subtitle,
                }
            )
            if len(items) >= max_items:
                return items

        for document in self._collected_document_results(state):
            if not isinstance(document, dict):
                continue
            document_id = str(document.get("document_id") or "").strip()
            if not document_id:
                continue
            dedupe_key = ("document", document_id)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            title = str(document.get("title") or "").strip() or f"Document {document_id}"
            subtitle = str(document.get("file_name") or "").strip() or None
            items.append(
                {
                    "entity_type": "document",
                    "entity_id": document_id,
                    "title": title,
                    "subtitle": subtitle,
                }
            )
            if len(items) >= max_items:
                break

        return items

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
            if call.tool_name != "get_events" or not call.success:
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
            if call.tool_name != "get_document" or not call.success:
                continue
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
        return collected

    def _compact_document_result(self, document: dict[str, Any]) -> dict[str, Any]:
        """Build a compact document result for response bundles."""
        raw_metadata_obj = document.get("raw_metadata")
        embedding_content = ""
        original_content = ""
        if isinstance(raw_metadata_obj, dict):
            embedding_content = str(raw_metadata_obj.get("content_english_for_embedding") or "")
            original_content = str(raw_metadata_obj.get("original_content") or "")
        preview_source = (
            document.get("content_preview") or embedding_content or original_content or ""
        )
        preview_text = str(preview_source or "").strip()
        if len(preview_text) > 12000:
            preview_text = preview_text[:11997].rstrip() + "..."

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
        if preview_text:
            compact["content_preview"] = preview_text
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
