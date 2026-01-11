"""
Main agent controller for bounded, reliable tool usage.

This is the core orchestration component that implements:
"The model proposes. The controller validates, executes, and decides when to continue or stop."

The controller:
1. Initializes state with the user's goal
2. Runs intent router for tool-set narrowing
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

import httpx
import requests

from .limits import AgentConfig, LimitChecker
from .router import IntentClassification, IntentRouter
from .state import AgentState, ToolCallRecord

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import tracing
from observability import trace


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
    def logger(self):
        """Lazy-load agent logger."""
        if self._logger is None:
            from observability.logger import get_logger
            self._logger = get_logger()
        return self._logger

    async def run(
        self,
        question: str,
        user_id: str = "default_user",
        session_id: Optional[str] = None,
        user_email: Optional[str] = None,
        conversation_history: Optional[list[dict[str, str]]] = None,
        search_limit: int = 5,
        event_capture_enabled: bool = False,
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
            event_capture_enabled: Whether to extract event proposals

        Returns:
            Response bundle with answer and metadata
        """
        total_start = perf_counter()
        run_id = self.logger.start_run(question, user_id, session_id)

        # Initialize state
        state = AgentState(goal=question)

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

            # Get allowed tools based on intent
            allowed_tools = (
                self.intent_router.get_allowed_tools(classification)
                if classification
                else self.intent_router.get_all_tools()
            )

            # Build initial messages
            messages = self._build_messages(
                question,
                state,
                conversation_history,
                user_email,
                search_limit,
                event_capture_enabled,
            )

            # Get tool definitions (filtered by intent)
            tools = self.tool_registry.get_tool_definitions(allowed_tools)

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
                    )
                    continue

                # Handle empty content
                if not content:
                    trace.trace_empty_response(state.step_count)
                    if state.step_count < 3:
                        messages.append({
                            "role": "user",
                            "content": "Please continue and provide your response.",
                        })
                        continue
                    # Give up after a few empty responses
                    content = "I apologize, but I wasn't able to complete this request."

                # Check if this is a continuation intent
                if self._looks_like_continuation(content) and state.step_count < self.config.max_steps - 1:
                    trace.trace_continuation_detected(content)
                    self.logger.log_continuation_detected(content)
                    messages.append({
                        "role": "user",
                        "content": "You expressed intent to perform an action but didn't call any tool. Please actually invoke the tool now.",
                    })
                    continue

                # Check for malformed tool call (JSON in content instead of proper tool call)
                if self._looks_like_malformed_tool_call(content) and state.step_count < self.config.max_steps - 1:
                    trace.trace_malformed_output(content, "JSON tool call in text output")
                    self.logger.log_malformed_output(content, "JSON tool call in text output")
                    messages.append({
                        "role": "user",
                        "content": "You output JSON instead of making a proper tool call. Do NOT output raw JSON. "
                        "Use the home_assistant tool with action='call_tool', tool_name set to the HA tool name "
                        "(e.g., 'HassLightSet'), and arguments containing the parameters. Try again.",
                    })
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
                    event_capture_enabled,
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
        search_limit: int = 5,
        event_capture_enabled: bool = False,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Stream agent responses with tool calling support.

        Yields events similar to the original answer_question_stream.
        """
        total_start = perf_counter()
        run_id = self.logger.start_run(question, user_id, session_id)

        state = AgentState(goal=question)

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

            allowed_tools = (
                self.intent_router.get_allowed_tools(classification)
                if classification
                else self.intent_router.get_all_tools()
            )

            messages = self._build_messages(
                question,
                state,
                conversation_history,
                user_email,
                search_limit,
                event_capture_enabled,
            )

            tools = self.tool_registry.get_tool_definitions(allowed_tools)
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

                    messages.append({
                        "role": "assistant",
                        "content": current_content,
                        "tool_calls": tool_calls,
                    })

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
                            call, state, question, search_limit, run_id
                        )

                        yield {
                            "type": "tool_result",
                            "name": func_name,
                            "result": result,
                        }

                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        })

                    continue

                if not current_content.strip():
                    messages.append({
                        "role": "user",
                        "content": "Please continue and provide your response.",
                    })
                    continue

                if self._looks_like_continuation(current_content) and state.step_count < self.config.max_steps - 1:
                    trace.trace_continuation_detected(current_content)
                    self.logger.log_continuation_detected(current_content)
                    if streamed_any:
                        yield {"type": "clear_content"}
                    messages.append({
                        "role": "user",
                        "content": "You expressed intent to perform an action but didn't call any tool. Please invoke the tool now.",
                    })
                    continue

                # Check for malformed tool call (JSON in content instead of proper tool call)
                if self._looks_like_malformed_tool_call(current_content) and state.step_count < self.config.max_steps - 1:
                    trace.trace_malformed_output(current_content, "JSON tool call in text output")
                    self.logger.log_malformed_output(current_content, "JSON tool call in text output")
                    if streamed_any:
                        yield {"type": "clear_content"}
                    messages.append({
                        "role": "user",
                        "content": "You output JSON instead of making a proper tool call. Do NOT output raw JSON. "
                        "Use the home_assistant tool with action='call_tool', tool_name set to the HA tool name "
                        "(e.g., 'HassLightSet'), and arguments containing the parameters. Try again.",
                    })
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
                event_capture_enabled,
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

        print(
            f"[controller] Intent: {classification.intent.value} "
            f"(confidence={classification.confidence:.2f}, duration={router_duration:.1f}ms)"
        )

        return classification

    def _build_messages(
        self,
        question: str,
        state: AgentState,
        conversation_history: Optional[list[dict[str, str]]],
        user_email: Optional[str],
        search_limit: int,
        event_capture_enabled: bool,
    ) -> list[dict[str, Any]]:
        """Build the message list for the LLM."""
        from prompts.context import (
            get_schema_hint,
            get_self_context,
            get_tag_context,
            get_time_context,
        )
        from prompts.state_injection import build_state_message
        from prompts.system import (
            get_bounded_agent_protocol,
            get_event_capture_prompt,
            get_system_prompt,
        )

        messages: list[dict[str, Any]] = []

        # System prompts
        messages.append({"role": "system", "content": get_system_prompt(search_limit)})

        # Schema hint
        schema_hint = get_schema_hint()
        if schema_hint:
            messages.append({"role": "system", "content": schema_hint})

        # Tag context
        tags_context = get_tag_context()
        if tags_context:
            messages.append({"role": "system", "content": tags_context})

        # Bounded agent protocol
        messages.append({"role": "system", "content": get_bounded_agent_protocol()})

        # Self context
        if user_email:
            self_context = get_self_context(user_email)
            if self_context:
                messages.append({"role": "system", "content": self_context})

        # Time context
        messages.append({"role": "system", "content": get_time_context()})

        # Event capture
        if event_capture_enabled:
            messages.append({"role": "system", "content": get_event_capture_prompt()})

        # Skills integration
        self._inject_skills(messages, question, conversation_history, state)

        # State injection
        messages.append(build_state_message(state))

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

                state.activated_skills.append({
                    "name": match.skill.name,
                    "confidence": match.confidence,
                })

        except Exception as e:
            print(f"[controller] Skills injection error: {e}")

    def _call_llm(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Make synchronous LLM call."""
        headers = {"Content-Type": "application/json"}
        if self.llm_api_key:
            headers["Authorization"] = f"Bearer {self.llm_api_key}"

        payload = {
            "model": self.llm_model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "stream": False,
        }

        response = requests.post(
            f"{self.llm_base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=self.llm_timeout,
        )
        response.raise_for_status()

        data = response.json()
        if "choices" in data and data["choices"]:
            return {"message": data["choices"][0].get("message", {})}
        return data

    async def _stream_llm(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream LLM responses."""
        headers = {"Content-Type": "application/json"}
        if self.llm_api_key:
            headers["Authorization"] = f"Bearer {self.llm_api_key}"

        payload = {
            "model": self.llm_model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "stream": True,
        }

        timeout = httpx.Timeout(self.llm_timeout, connect=10.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self.llm_base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()
                accumulated_tool_calls: dict[int, dict[str, Any]] = {}

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line == "data: [DONE]":
                        continue
                    if line.startswith("data: "):
                        line = line[6:]

                    try:
                        chunk = json.loads(line)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        finish_reason = chunk.get("choices", [{}])[0].get("finish_reason")

                        if "tool_calls" in delta:
                            for tc in delta["tool_calls"]:
                                idx = tc.get("index", 0)
                                if idx not in accumulated_tool_calls:
                                    accumulated_tool_calls[idx] = {
                                        "id": tc.get("id", ""),
                                        "type": "function",
                                        "function": {"name": "", "arguments": ""},
                                    }
                                if tc.get("id"):
                                    accumulated_tool_calls[idx]["id"] = tc["id"]
                                if "function" in tc:
                                    if tc["function"].get("name"):
                                        accumulated_tool_calls[idx]["function"]["name"] = tc["function"]["name"]
                                    if tc["function"].get("arguments"):
                                        accumulated_tool_calls[idx]["function"]["arguments"] += tc["function"]["arguments"]

                        normalized = {"message": {"content": delta.get("content", "")}}

                        if finish_reason in ("tool_calls", "stop") and accumulated_tool_calls:
                            normalized["message"]["tool_calls"] = list(accumulated_tool_calls.values())
                            normalized["done"] = True
                        elif finish_reason == "stop":
                            normalized["done"] = True

                        yield normalized

                    except json.JSONDecodeError:
                        continue

    async def _handle_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        state: AgentState,
        messages: list[dict[str, Any]],
        question: str,
        search_limit: int,
        run_id: str,
    ) -> None:
        """Handle tool calls with validation."""
        # Add assistant message with tool calls
        messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": tool_calls,
        })

        for call in tool_calls:
            result = await self._execute_tool_call(
                call, state, question, search_limit, run_id
            )

            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id"),
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

    async def _execute_tool_call(
        self,
        call: dict[str, Any],
        state: AgentState,
        question: str,
        search_limit: int,
        run_id: str,
    ) -> dict[str, Any]:
        """Execute a single tool call with validation."""
        func = call.get("function", {})
        tool_name = func.get("name", "")
        raw_args = func.get("arguments", "{}")

        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            trace.trace_tool_error(tool_name, f"Invalid JSON arguments: {raw_args}")
            return {"error": f"Invalid JSON arguments: {raw_args}"}

        # Trace tool call start
        trace.trace_tool_call_start(tool_name, args)

        # Pre-execution validation
        if self.config.enable_validation:
            trace.trace_pre_validation_start(tool_name)
            validation = self.pre_validator.validate(tool_name, args)
            if not validation.valid:
                state.repair_count += 1
                trace.trace_pre_validation_fail(tool_name, validation.errors, state.repair_count)
                self.logger.log_tool_call(
                    run_id,
                    state.step_count,
                    tool_name,
                    args,
                    pre_validation_passed=False,
                    validation_errors=validation.errors,
                    repair_attempt=state.repair_count,
                )
                return validation.to_feedback()
            trace.trace_pre_validation_pass(tool_name)

        # Execute tool
        step_start = perf_counter()
        result = self._execute_handler(tool_name, args, state, question, search_limit)
        duration_ms = (perf_counter() - step_start) * 1000

        # Determine result summary for tracing
        success = "error" not in result and result.get("success") is not False
        if result.get("error"):
            result_summary = f"Error: {result.get('error')}"
        elif "count" in result:
            result_summary = f"{result['count']} items"
        elif "tools" in result:
            result_summary = f"Listed {len(result['tools'])} tools"
        elif "rows" in result:
            result_summary = f"{len(result['rows'])} rows"
        elif "results" in result:
            result_summary = f"{len(result['results'])} results"
        else:
            result_summary = "OK" if success else "Failed"

        trace.trace_tool_execution_result(tool_name, duration_ms, success, result_summary)
        if not success:
            trace.trace_tool_error(tool_name, result.get("error", "Unknown error"))

        # Record tool call
        record = ToolCallRecord(
            tool_name=tool_name,
            arguments=args,
            result=result,
            duration_ms=duration_ms,
            success=success,
            error=result.get("error"),
        )
        state.record_tool_call(record)

        # Log tool call
        self.logger.log_tool_call(
            run_id,
            state.step_count,
            tool_name,
            args,
            duration_ms=duration_ms,
            result=result,
        )

        # Post-execution validation (for fact extraction AND failure detection)
        if self.config.enable_validation:
            from tools.validators.post_execution import GoalCoverage

            trace.trace_post_validation_start(tool_name)
            post_result = self.post_validator.validate(
                tool_name,
                args,
                result,
                state.goal,
                state.known_facts,
            )

            # Trace post-validation result
            trace.trace_post_validation_result(
                tool_name,
                coverage=post_result.coverage.value if post_result.coverage else "unknown",
                passed=post_result.coverage != GoalCoverage.FAILED,
                reason=post_result.reason,
                suggested_tools=post_result.suggested_next_tools,
            )
            self.logger.log_validation_result(
                validation_type="Post-execution",
                passed=post_result.coverage != GoalCoverage.FAILED,
                coverage=post_result.coverage.value if post_result.coverage else None,
                reason=post_result.reason,
                suggested_tools=post_result.suggested_next_tools,
            )

            # Extract facts from result
            for fact in post_result.extracted_facts:
                state.add_fact(fact)
                trace.trace_fact_extracted(fact)
                self.logger.log_state_update("Fact added", fact)

            # If tool failed, enhance the result with guidance
            if post_result.coverage == GoalCoverage.FAILED:
                guidance = self._get_failure_guidance(tool_name, result, post_result)
                self.logger.log_decision(
                    decision="Tool call failed - injecting recovery guidance",
                    reason=post_result.reason,
                    details={"guidance": guidance[:100] + "..." if len(guidance) > 100 else guidance},
                )
                result["_validation"] = {
                    "status": "failed",
                    "reason": post_result.reason,
                    "guidance": guidance,
                }
                # Add suggested alternatives if available
                if post_result.suggested_next_tools:
                    result["_validation"]["suggested_tools"] = post_result.suggested_next_tools

        return result

    def _get_failure_guidance(
        self,
        tool_name: str,
        result: dict[str, Any],
        post_result,
    ) -> str:
        """Generate guidance message for failed tool calls."""
        if tool_name == "home_assistant":
            error = result.get("error", "")
            if "not found" in error.lower() or "unknown" in error.lower():
                return (
                    "The Home Assistant tool call failed because the tool name was not recognized. "
                    "You MUST call home_assistant with action='list_tools' FIRST to discover "
                    "the actual available tool names for this installation. Do NOT guess tool names."
                )
            return (
                "The Home Assistant call failed. Review the error and try again with "
                "corrected arguments. If unsure, call action='list_tools' to see available tools."
            )

        return f"Tool '{tool_name}' failed: {post_result.reason}. Review and retry with corrected parameters."

    def _execute_handler(
        self,
        tool_name: str,
        args: dict[str, Any],
        state: AgentState,
        question: str,
        search_limit: int,
    ) -> dict[str, Any]:
        """Execute tool handler."""
        from tools.handlers import get_handler

        handler = get_handler(tool_name)
        if not handler:
            return {"error": f"Unknown tool: {tool_name}"}

        try:
            return handler(
                args,
                state=state,
                question=question,
                search_limit=search_limit,
            )
        except Exception as e:
            print(f"[controller] Tool execution error: {e}")
            return {"error": str(e)}

    def _looks_like_continuation(self, content: str) -> bool:
        """Check if content indicates model wants to continue but didn't call a tool."""
        patterns = [
            "let me try", "let me find", "let me search",
            "let me check", "let me look", "let me query",
            "i need to", "i will try", "i'll try",
            "i will search", "i'll search", "i will query",
            "first, i need", "i should", "i'll need to",
        ]

        lower = content.lower().strip()
        if len(lower) > 800:
            return False

        return any(p in lower for p in patterns)

    def _looks_like_malformed_tool_call(self, content: str) -> bool:
        """Check if content looks like a malformed tool call (JSON output instead of proper tool call)."""
        stripped = content.strip()

        # Remove common LLM artifacts that prefix JSON
        for prefix in ["<|python_tag|>", "```json", "```", "<tool_call>", "<function_call>"]:
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):].strip()
        for suffix in ["```", "</tool_call>", "</function_call>"]:
            if stripped.endswith(suffix):
                stripped = stripped[:-len(suffix)].strip()

        # Check for JSON-like tool call patterns
        if stripped.startswith("{") and stripped.endswith("}"):
            # Quick check for common tool call indicators
            tool_indicators = [
                '"type": "function"',
                '"name":',
                '"function":',
                '"parameters":',
                '"arguments":',
                '"tool_call"',
                '"action":',
                "HassLightSet",
                "HassClimateSet",
                "HassTurnOn",
                "HassTurnOff",
                "home_assistant",
            ]
            return any(indicator in stripped for indicator in tool_indicators)

        return False

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
            "search_results": state.search_results,
            "detailed_events": state.detailed_events,
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
        event_capture_enabled: bool,
    ) -> dict[str, Any]:
        """Finalize the response."""
        # Extract event proposal if enabled
        event_proposal = None
        if event_capture_enabled:
            event_proposal = self._extract_event_proposal(answer)
            if event_proposal:
                answer = self._strip_event_proposal(answer)

        # Log completion
        self.logger.complete_run(run_id, success=True, final_answer=answer)

        duration_ms = (perf_counter() - total_start) * 1000

        # Trace run completion
        trace.trace_run_complete(
            run_id,
            success=True,
            duration_ms=duration_ms,
            steps=state.step_count,
            tool_calls=state.tool_calls_count,
            answer_preview=answer[:200] if answer else None,
        )

        bundle = {
            "question": question,
            "answer": answer,
            "thread_id": session_id,
            "resolution": state.resolution,
            "search_results": state.search_results,
            "detailed_events": state.detailed_events,
        }

        if event_proposal:
            bundle["event_proposal"] = event_proposal

        if state.activated_skills:
            bundle["activated_skills"] = [s.get("name") for s in state.activated_skills]

        return bundle

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
            json_str = content[start_idx + len(start):end_idx].strip()
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
        after = content[end_idx + len(end):].lstrip()

        return (before + " " + after).strip() if after else before


# Singleton controller instance
_controller: Optional[AgentController] = None


def get_controller() -> AgentController:
    """Get the singleton controller instance."""
    global _controller
    if _controller is None:
        _controller = AgentController()
    return _controller
