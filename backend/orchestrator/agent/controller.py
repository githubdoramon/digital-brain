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
from time import perf_counter
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
import requests

from .state import AgentState, ToolCallRecord
from .limits import AgentConfig, LimitChecker
from .router import IntentRouter, IntentClassification

# Import with absolute paths to avoid circular imports
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
        conversation_history: Optional[List[Dict[str, str]]] = None,
        search_limit: int = 5,
        event_capture_enabled: bool = False,
    ) -> Dict[str, Any]:
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
                should_stop, violation = self.limit_checker.should_stop(state)
                if should_stop:
                    return self._handle_limit_violation(
                        state, violation, run_id, session_id, total_start
                    )

                state.step_count += 1
                step_start = perf_counter()

                # Log step start
                self.logger.start_step(run_id, state.step_count)

                # Call LLM
                llm_start = perf_counter()
                response = self._call_llm(messages, tools)
                llm_duration = (perf_counter() - llm_start) * 1000

                message = response.get("message", {})
                content = (message.get("content") or "").strip()
                tool_calls = message.get("tool_calls") or []

                # Log LLM call
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
                    messages.append({
                        "role": "user",
                        "content": "You expressed intent to perform an action but didn't call any tool. Please actually invoke the tool now.",
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
            print(f"[controller] Error in agent loop: {e}")
            self.logger.complete_run(run_id, success=False, error=str(e))
            raise

    async def run_stream(
        self,
        question: str,
        user_id: str = "default_user",
        session_id: Optional[str] = None,
        user_email: Optional[str] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        search_limit: int = 5,
        event_capture_enabled: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream agent responses with tool calling support.

        Yields events similar to the original answer_question_stream.
        """
        total_start = perf_counter()
        run_id = self.logger.start_run(question, user_id, session_id)

        state = AgentState(goal=question)

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
                should_stop, violation = self.limit_checker.should_stop(state)
                if should_stop:
                    yield {
                        "type": "status",
                        "message": f"Limit reached: {violation.message}",
                    }
                    break

                state.step_count += 1
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
                    if streamed_any:
                        yield {"type": "clear_content"}
                    messages.append({
                        "role": "user",
                        "content": "You expressed intent to perform an action but didn't call any tool. Please invoke the tool now.",
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
            print(f"[controller] Stream error: {e}")
            self.logger.complete_run(run_id, success=False, error=str(e))
            yield {"type": "error", "message": str(e)}

    async def _run_intent_router(
        self,
        question: str,
        conversation_history: Optional[List[Dict[str, str]]],
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
        conversation_history: Optional[List[Dict[str, str]]],
        user_email: Optional[str],
        search_limit: int,
        event_capture_enabled: bool,
    ) -> List[Dict[str, Any]]:
        """Build the message list for the LLM."""
        from prompts.system import get_system_prompt, get_bounded_agent_protocol, get_event_capture_prompt
        from prompts.context import get_time_context, get_tag_context, get_self_context, get_schema_hint
        from prompts.state_injection import build_state_message

        messages: List[Dict[str, Any]] = []

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
        messages: List[Dict[str, Any]],
        question: str,
        conversation_history: Optional[List[Dict[str, str]]],
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
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
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
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
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
                accumulated_tool_calls: Dict[int, Dict[str, Any]] = {}

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
        tool_calls: List[Dict[str, Any]],
        state: AgentState,
        messages: List[Dict[str, Any]],
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
        call: Dict[str, Any],
        state: AgentState,
        question: str,
        search_limit: int,
        run_id: str,
    ) -> Dict[str, Any]:
        """Execute a single tool call with validation."""
        func = call.get("function", {})
        tool_name = func.get("name", "")
        raw_args = func.get("arguments", "{}")

        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            return {"error": f"Invalid JSON arguments: {raw_args}"}

        # Pre-execution validation
        if self.config.enable_validation:
            validation = self.pre_validator.validate(tool_name, args)
            if not validation.valid:
                state.repair_count += 1
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

        # Execute tool
        step_start = perf_counter()
        result = self._execute_handler(tool_name, args, state, question, search_limit)
        duration_ms = (perf_counter() - step_start) * 1000

        # Record tool call
        record = ToolCallRecord(
            tool_name=tool_name,
            arguments=args,
            result=result,
            duration_ms=duration_ms,
            success="error" not in result,
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

        # Post-execution validation (for fact extraction)
        if self.config.enable_validation:
            post_result = self.post_validator.validate(
                tool_name,
                args,
                result,
                state.goal,
                state.known_facts,
            )
            for fact in post_result.extracted_facts:
                state.add_fact(fact)

        return result

    def _execute_handler(
        self,
        tool_name: str,
        args: Dict[str, Any],
        state: AgentState,
        question: str,
        search_limit: int,
    ) -> Dict[str, Any]:
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

    def _handle_limit_violation(
        self,
        state: AgentState,
        violation,
        run_id: str,
        session_id: Optional[str],
        total_start: float,
    ) -> Dict[str, Any]:
        """Handle when a limit is violated."""
        message = self.limit_checker.format_stop_message(state, violation)

        self.logger.complete_run(
            run_id,
            success=False,
            final_answer=message,
            limit_hit=violation.limit_type.value,
        )

        duration_ms = (perf_counter() - total_start) * 1000
        print(f"[controller] Limit hit: {violation.limit_type.value} after {duration_ms:.1f}ms")

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
    ) -> Dict[str, Any]:
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
        print(
            f"[controller] Completed in {duration_ms:.1f}ms, "
            f"steps={state.step_count}, tool_calls={state.tool_calls_count}"
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

    def _extract_event_proposal(self, content: str) -> Optional[Dict[str, Any]]:
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
