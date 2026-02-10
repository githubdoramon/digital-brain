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
from time import perf_counter
from typing import Any, Optional

from observability import trace
from observability.logger import get_runtime_logger
from ui_dsl.clarification import extract_need_user_input

from .contact_resolution import (
    build_contact_clarification_result,
    get_user_clarification_prompt_for_contact_resolution,
    is_contact_referential_memory_query,
    resolve_contacts_for_text,
    should_pre_resolve_contacts,
)
from .guardrails import (
    build_contact_scope_context,
    detect_future_temporal_intent,
    detect_temporal_sort_order,
    optimize_query_for_scoped_contacts,
    sanitize_goal_text,
    utc_now_iso,
)
from .limits import AgentConfig, LimitChecker
from .llm_transport import call_llm_with_tools, stream_llm_with_tools
from .response_guardrails import (
    CODE_DESCRIBING_TOOL_PROMPT,
    CONTINUATION_PROMPT_STREAM,
    CONTINUATION_PROMPT_SYNC,
    MALFORMED_TOOL_CALL_PROMPT,
    looks_like_code_describing_tool,
    looks_like_continuation,
    looks_like_malformed_tool_call,
)
from .router import IntentClassification, IntentRouter
from .state import AgentState

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
    ):
        """
        Initialize the agent controller.

        Args:
            config: Agent configuration (uses env vars if not provided)
            intent_router: Intent router (created if not provided)
        """
        self.config = config or AgentConfig.from_env()
        self.intent_router = intent_router or IntentRouter()
        self.limit_checker = LimitChecker(self.config)

        # LLM configuration
        self.llm_base_url = os.getenv("LLM_BASE_URL", "")
        self.llm_model = os.getenv("LLM_CHAT_MODEL", "")
        self.llm_api_key = os.getenv("LLM_API_KEY", "")
        self.llm_timeout = int(os.getenv("LLM_TIMEOUT", "120"))

        # Lazy-loaded components
        self._tool_registry = None
        self._pre_validator = None
        self._post_validator = None
        self._logger = None
        self._tool_executor = None

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
        total_start = perf_counter()
        run_id = self.logger.start_run(question, user_id, session_id)

        # Initialize state
        state = AgentState(goal=question)
        state.request_context = self._normalize_client_context(client_context)
        normalized_submission = self._normalize_ui_submission(ui_submission)
        if normalized_submission:
            state.request_context["ui_submission"] = normalized_submission

        # Trace run start
        trace.trace_run_start(question, run_id)

        try:
            # Phase 1: Intent routing
            if self.config.enable_intent_routing:
                classification = await self._run_intent_router(
                    question, conversation_history, run_id
                )
                state.intent = classification.intent.value
                state.allowed_tool_groups = classification.allowed_tool_groups
                state.constraints = classification.constraints
                state.skill_hints = classification.skill_hints
            else:
                classification = None

            clarification_prompt = None
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
                trace.trace_contact_resolution_outcome(
                    "clarification_returned",
                    {"source": "pre_resolution"},
                )
                return self._finalize(
                    question,
                    clarification_prompt,
                    state,
                    run_id,
                    session_id,
                    total_start,
                )

            # Build initial messages
            messages = self._build_messages(
                question,
                state,
                conversation_history,
                user_email,
                search_limit,
                state.request_context,
            )

            # Expose full tool set; no intent-based narrowing.
            tools = self.tool_registry.get_tool_definitions()

            # Phase 2: Agent loop
            while True:
                # Check limits
                trace.trace_limit_check(
                    state.step_count,
                    self.config.max_steps,
                    state.tool_calls_count,
                    self.config.max_tool_calls,
                    state.repair_count,
                    self.config.max_repairs,
                )
                should_stop, violation = self.limit_checker.should_stop(state)
                if should_stop:
                    return self._handle_limit_violation(
                        state, violation, run_id, session_id, total_start
                    )

                state.step_count += 1

                # Trace step start
                trace.trace_step_start(
                    state.step_count,
                    state.tool_calls_count,
                    len(state.known_facts),
                )
                self.logger.start_step(run_id, state.step_count)

                # Call LLM
                trace.trace_llm_request(len(tools))
                llm_start = perf_counter()
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

                    clarification_prompt = get_user_clarification_prompt_for_contact_resolution(
                        state
                    )
                    if clarification_prompt:
                        trace.trace_contact_resolution_outcome(
                            "clarification_returned",
                            {"source": "tool_loop"},
                        )
                        trace.trace_decision(
                            "Need user clarification",
                            "Returning clarification prompt to user",
                            {"prompt": clarification_prompt},
                        )
                        self.logger.log_decision(
                            decision="Requesting clarification",
                            reason=clarification_prompt,
                        )
                        return self._finalize(
                            question,
                            clarification_prompt,
                            state,
                            run_id,
                            session_id,
                            total_start,
                        )

                    if state.ui_directives and state.pending_questions:
                        follow_up_prompt = state.pending_questions[-1]
                        trace.trace_decision(
                            "Returning UI follow-up",
                            "Structured directive requires user input",
                            {"prompt": follow_up_prompt},
                        )
                        self.logger.log_decision(
                            decision="Requesting UI follow-up",
                            reason=follow_up_prompt,
                        )
                        return self._finalize(
                            question,
                            follow_up_prompt,
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
                    content = "I apologize, but I wasn't able to complete this request."

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
                if (
                    looks_like_malformed_tool_call(content)
                    and state.step_count < self.config.max_steps - 1
                ):
                    trace.trace_malformed_output(content, "JSON tool call in text output")
                    self.logger.log_malformed_output(content, "JSON tool call in text output")
                    messages.append(
                        {
                            "role": "user",
                            "content": MALFORMED_TOOL_CALL_PROMPT,
                        }
                    )
                    continue

                # Check for code describing tool usage instead of actual tool call
                if (
                    looks_like_code_describing_tool(content)
                    and state.step_count < self.config.max_steps - 1
                ):
                    trace.trace_malformed_output(
                        content, "Code describing tool instead of calling it"
                    )
                    self.logger.log_malformed_output(
                        content, "Code describing tool instead of calling it"
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": CODE_DESCRIBING_TOOL_PROMPT,
                        }
                    )
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
                    self.logger.log_decision(
                        decision="Forcing continuation",
                        reason=goal_check["reason"],
                        details={"pending_actions": goal_check["pending_actions"]},
                    )
                    # Inject a generic prompt to force the LLM to complete the action
                    messages.append(
                        {
                            "role": "user",
                            "content": f"INCOMPLETE: You have not completed the user's request yet. "
                            f"Status: {goal_check['reason']}. "
                            f"Required: {goal_check['pending_actions'][0]}. "
                            f"Do NOT respond to the user - FIRST invoke the appropriate tool to complete the action.",
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
        total_start = perf_counter()
        run_id = self.logger.start_run(question, user_id, session_id)

        state = AgentState(goal=question)
        state.request_context = self._normalize_client_context(client_context)
        normalized_submission = self._normalize_ui_submission(ui_submission)
        if normalized_submission:
            state.request_context["ui_submission"] = normalized_submission

        # Trace run start
        trace.trace_run_start(question, run_id)

        try:
            # Intent routing
            if self.config.enable_intent_routing:
                classification = await self._run_intent_router(
                    question, conversation_history, run_id
                )
                state.intent = classification.intent.value
                state.allowed_tool_groups = classification.allowed_tool_groups
                state.constraints = classification.constraints
                state.skill_hints = classification.skill_hints
            else:
                classification = None

            clarification_prompt = None
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
                trace.trace_contact_resolution_outcome(
                    "clarification_returned",
                    {"source": "pre_resolution_stream"},
                )
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

            messages = self._build_messages(
                question,
                state,
                conversation_history,
                user_email,
                search_limit,
                state.request_context,
            )

            tools = self.tool_registry.get_tool_definitions()
            accumulated_content = ""

            while True:
                # Check limits
                trace.trace_limit_check(
                    state.step_count,
                    self.config.max_steps,
                    state.tool_calls_count,
                    self.config.max_tool_calls,
                    state.repair_count,
                    self.config.max_repairs,
                )
                should_stop, violation = self.limit_checker.should_stop(state)
                if should_stop:
                    trace.trace_limit_violation(
                        violation.limit_type.value,
                        violation.message,
                        {"steps": state.step_count, "tool_calls": state.tool_calls_count},
                    )
                    yield {
                        "type": "status",
                        "message": f"Limit reached: {violation.message}",
                    }
                    break

                state.step_count += 1
                trace.trace_step_start(
                    state.step_count,
                    state.tool_calls_count,
                    len(state.known_facts),
                )
                yield {"type": "status", "message": f"Thinking (step {state.step_count})..."}

                tool_calls = []
                current_content = ""
                streamed_any = False

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

                if tool_calls:
                    if streamed_any:
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
                            "args": func_args if isinstance(func_args, dict) else {},
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

                    clarification_prompt = get_user_clarification_prompt_for_contact_resolution(
                        state
                    )
                    if clarification_prompt:
                        trace.trace_contact_resolution_outcome(
                            "clarification_returned",
                            {"source": "tool_loop_stream"},
                        )
                        trace.trace_decision(
                            "Need user clarification (stream)",
                            "Returning clarification prompt to user",
                            {"prompt": clarification_prompt},
                        )
                        accumulated_content = clarification_prompt
                        yield {"type": "token", "content": clarification_prompt}
                        break

                    if state.ui_directives and state.pending_questions:
                        follow_up_prompt = state.pending_questions[-1]
                        trace.trace_decision(
                            "Returning UI follow-up (stream)",
                            "Structured directive requires user input",
                            {"prompt": follow_up_prompt},
                        )
                        accumulated_content = follow_up_prompt
                        yield {"type": "token", "content": follow_up_prompt}
                        break

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
                if (
                    looks_like_malformed_tool_call(current_content)
                    and state.step_count < self.config.max_steps - 1
                ):
                    trace.trace_malformed_output(current_content, "JSON tool call in text output")
                    self.logger.log_malformed_output(
                        current_content, "JSON tool call in text output"
                    )
                    if streamed_any:
                        yield {"type": "clear_content"}
                    messages.append(
                        {
                            "role": "user",
                            "content": MALFORMED_TOOL_CALL_PROMPT,
                        }
                    )
                    continue

                # Check for code describing tool usage instead of actual tool call
                if (
                    looks_like_code_describing_tool(current_content)
                    and state.step_count < self.config.max_steps - 1
                ):
                    trace.trace_malformed_output(
                        current_content, "Code describing tool instead of calling it"
                    )
                    self.logger.log_malformed_output(
                        current_content, "Code describing tool instead of calling it"
                    )
                    if streamed_any:
                        yield {"type": "clear_content"}
                    messages.append(
                        {
                            "role": "user",
                            "content": CODE_DESCRIBING_TOOL_PROMPT,
                        }
                    )
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
                            "content": f"INCOMPLETE: You have not completed the user's request yet. "
                            f"Status: {goal_check['reason']}. "
                            f"Required: {goal_check['pending_actions'][0]}. "
                            f"Do NOT respond to the user - FIRST invoke the appropriate tool to complete the action.",
                        }
                    )
                    continue

                accumulated_content = current_content
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
            classification.skill_hints,
        )

        logger.info(
            "[controller] Intent: %s (confidence=%.2f, duration=%.1fms)",
            classification.intent.value,
            classification.confidence,
            router_duration,
        )

        return classification

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
                lat = round(float(location.get("lat")), 3)
                lon = round(float(location.get("lon")), 3)
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    normalized_location: dict[str, Any] = {"lat": lat, "lon": lon}

                    accuracy = location.get("accuracy_m")
                    if accuracy is not None:
                        try:
                            normalized_location["accuracy_m"] = round(float(accuracy), 1)
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
        """Build the message list for the LLM."""
        from prompts.clarification import get_clarification_skill_prompt_block
        from prompts.context import (
            get_location_context,
            get_self_context,
            get_tag_context,
            get_time_context,
        )
        from prompts.state_injection import build_state_message
        from prompts.system import (
            get_bounded_agent_protocol,
            get_system_prompt,
        )

        messages: list[dict[str, Any]] = []

        # System prompts
        messages.append({"role": "system", "content": get_system_prompt(search_limit)})

        # Tag context
        tags_context = get_tag_context()
        if tags_context:
            messages.append({"role": "system", "content": tags_context})

        # Bounded agent protocol
        messages.append({"role": "system", "content": get_bounded_agent_protocol()})

        # Clarification skill (always injected for consistent follow-up behavior)
        clarification_skill_block = get_clarification_skill_prompt_block()
        if clarification_skill_block:
            messages.append({"role": "system", "content": clarification_skill_block})

        # Self context
        if user_email:
            self_context = get_self_context(user_email)
            if self_context:
                messages.append({"role": "system", "content": self_context})

        # Time context
        messages.append({"role": "system", "content": get_time_context()})

        # Client context (timezone/locale/location)
        location_context = get_location_context(client_context)
        if location_context:
            messages.append({"role": "system", "content": location_context})

        # Skills integration
        self._inject_skills(messages, question, conversation_history, state)

        # State injection
        messages.append(build_state_message(state))

        # Explicit contact-scope injection for tool planning.
        contact_scope_context = build_contact_scope_context(
            state.resolution.get("active_contact_scope") or []
        )
        if contact_scope_context:
            messages.append({"role": "system", "content": contact_scope_context})

        # Conversation history
        if conversation_history:
            for msg in conversation_history:
                messages.append({"role": msg["role"], "content": msg["content"]})

        # User question
        messages.append({"role": "user", "content": question.strip()})

        return messages

    def _inject_skills(
        self,
        messages: list[dict[str, Any]],
        question: str,
        conversation_history: Optional[list[dict[str, str]]],
        state: AgentState,
    ) -> None:
        """Inject matching skills into messages."""
        try:
            import skills

            registry = skills.get_registry()

            # Skill index
            skill_index = registry.get_skill_index()
            if skill_index:
                messages.append({"role": "system", "content": skill_index})

            # Find matching skills (with hints from router if available)
            matching_skills = registry.find_matching_skills(
                query=question,
                conversation_history=conversation_history,
            )

            for match in matching_skills:
                skill_prompt = (
                    f"ACTIVE SKILL [{match.skill.name}] (confidence: {match.confidence:.2f}):\n"
                    f"{match.skill.instructions}"
                )
                messages.append({"role": "system", "content": skill_prompt})

                state.activated_skills.append(
                    {
                        "name": match.skill.name,
                        "confidence": match.confidence,
                    }
                )

        except Exception as e:
            logger.exception("[controller] Skills injection error: %s", e)

    def _call_llm(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Make synchronous LLM call."""
        return call_llm_with_tools(
            messages,
            tools,
            model=self.llm_model or None,
            timeout=self.llm_timeout,
        )

    async def _stream_llm(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream LLM responses."""
        async for chunk in stream_llm_with_tools(
            messages,
            tools,
            model=self.llm_model or None,
            timeout=self.llm_timeout,
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
                "status": "success",
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
                "status": "need_user_input",
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
        last_status = last_result.get("status")
        if not last_status and extract_need_user_input(last_result, default_source="resolve_contacts"):
            last_status = "need_user_input"
        if last_text.lower() != text.lower():
            return None
        if last_status not in {"need_user_input", "no_people"}:
            return None

        reason = (
            "Contact resolution already returned ambiguity for this exact text. "
            "Ask the user to clarify instead of retrying the same call."
            if last_status == "need_user_input"
            else "No people were detected for this text in the previous attempt."
        )
        trace.trace_decision(
            "Blocked redundant resolve_contacts call",
            reason,
            {"text": text, "previous_status": last_status},
        )
        return {
            **last_result,
            "status": last_status,
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
        is_future_temporal_query = detect_future_temporal_intent(
            query_text
        ) or detect_future_temporal_intent(goal_text)
        if is_future_temporal_query and not normalized_args.get("time_start"):
            normalized_args["time_start"] = utc_now_iso()
        if (
            sort_order in {"newest", "oldest"}
            and not is_future_temporal_query
            and not normalized_args.get("time_end")
        ):
            normalized_args["time_end"] = utc_now_iso()
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
                status = resolution.get("status")
                if not status and extract_need_user_input(
                    resolution,
                    default_source="resolve_contacts",
                ):
                    status = "need_user_input"
                if status == "need_user_input":
                    preempt = build_contact_clarification_result(
                        ambiguous_contacts=state.resolution.get(
                            "pending_contact_ambiguous_contacts", []
                        ),
                        people_mentioned=state.resolution.get("pending_contact_people", []),
                    )
                    return normalized_args, preempt

                if status == "success":
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

        same_scope = (
            tuple(sorted(str(cid) for cid in (reference_args.get("contact_ids") or [])))
            == tuple(sorted(str(cid) for cid in (args.get("contact_ids") or [])))
        )
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

        status = resolution.get("status")
        if not status and extract_need_user_input(
            resolution,
            default_source="resolve_contacts",
        ):
            status = "need_user_input"
        if status == "success":
            scope_ids = state.resolution.get("active_contact_scope_ids", [])
            if scope_ids:
                state.add_fact(f"Pre-resolved {len(scope_ids)} contact(s) from user question")
        elif status == "need_user_input":
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
        status = result.get("status")
        if not status and need_user_input:
            status = "need_user_input"

        state.resolution["last_contact_resolution_text"] = args.get("text", "")
        state.resolution["last_contact_resolution_status"] = status
        if status == "success":
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
            else:
                state.resolution.pop("active_contact_scope", None)
            return

        if status == "need_user_input":
            ambiguous_contacts = result.get("ambiguous_contacts", [])
            prompt = str((need_user_input or {}).get("prompt") or "").strip()
            if not prompt:
                prompt = "I found multiple matching people. Please clarify which one you mean."
            if need_user_input:
                state.resolution["pending_contact_need_user_input"] = need_user_input
            else:
                state.resolution["pending_contact_need_user_input"] = {
                    "kind": "disambiguation",
                    "prompt": prompt,
                    "submission_mode": "text",
                }
            state.resolution["pending_contact_ambiguous_contacts"] = ambiguous_contacts
            state.resolution["pending_contact_people"] = result.get("people_mentioned", [])
            state.resolution["pending_contact_scope_text"] = args.get("text", "")
            state.resolution.pop("active_contact_scope_ids", None)
            state.resolution.pop("active_contact_scope_text", None)
            state.resolution.pop("active_contact_scope", None)
            return

        if status == "no_people":
            # No person context found; clear scoped contact state.
            state.resolution.pop("active_contact_scope_ids", None)
            state.resolution.pop("active_contact_scope_text", None)
            state.resolution.pop("active_contact_scope", None)
            state.resolution.pop("pending_contact_need_user_input", None)
            state.resolution.pop("pending_contact_ambiguous_contacts", None)
            state.resolution.pop("pending_contact_people", None)
            state.resolution.pop("pending_contact_scope_text", None)

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

        # Log the limit violation decision
        self.logger.log_decision(
            decision="Stopping due to limit violation",
            reason=violation.message,
            details={
                "limit_type": violation.limit_type.value,
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
            "ui_directives": state.ui_directives,
            "limit_hit": violation.limit_type.value,
        }

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
            trace.trace_decision(
                "Empty response with tool calls",
                "Generated fallback response",
                {"tool_calls": state.tool_calls_count},
            )
            # Generate a minimal response based on what was done
            if state.completion_evidence:
                answer = f"Done. {state.completion_evidence[-1]}"
            elif state.known_facts:
                answer = f"Based on my search: {state.known_facts[-1]}"
            else:
                answer = "I completed the requested action."

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
            },
        }

        if state.activated_skills:
            bundle["activated_skills"] = [s.get("name") for s in state.activated_skills]

        return bundle

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
        raw_metadata = (
            document.get("raw_metadata")
            if isinstance(document.get("raw_metadata"), dict)
            else {}
        )
        preview_source = (
            document.get("content_preview")
            or raw_metadata.get("content_english_for_embedding")
            or raw_metadata.get("original_content")
            or ""
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


# Singleton controller instance
_controller: Optional[AgentController] = None


def get_controller() -> AgentController:
    """Get the singleton controller instance."""
    global _controller
    if _controller is None:
        _controller = AgentController()
    return _controller
