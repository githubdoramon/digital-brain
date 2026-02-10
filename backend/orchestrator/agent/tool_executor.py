"""Tool-call orchestration and validation pipeline for the main agent."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from observability import trace
from observability.logger import get_runtime_logger
from ui_dsl.clarification import extract_need_user_input

from .state import AgentState, ToolCallRecord

logger = get_runtime_logger(__name__)


class ToolExecutionCoordinator:
    """Execute tool calls with guardrails, validation, and state bookkeeping."""

    def __init__(self, controller: Any):
        self.controller = controller

    async def handle_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        state: AgentState,
        messages: list[dict[str, Any]],
        question: str,
        search_limit: int,
        run_id: str,
        user_email: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> None:
        """Handle a batch of tool calls and append tool results to messages."""
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": tool_calls,
            }
        )

        for call in tool_calls:
            function = call.get("function", {})
            tool_name = str(function.get("name") or "").strip()
            result = await self.execute_tool_call(
                call=call,
                state=state,
                question=question,
                search_limit=search_limit,
                run_id=run_id,
                user_email=user_email,
                conversation_history=conversation_history,
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(
                        self.build_tool_message_payload(tool_name, result),
                        ensure_ascii=False,
                        default=str,
                    ),
                }
            )

    async def execute_tool_call(
        self,
        call: dict[str, Any],
        state: AgentState,
        question: str,
        search_limit: int,
        run_id: str,
        user_email: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Execute a single tool call with validation."""
        func = call.get("function", {})
        tool_name = func.get("name", "")
        raw_args = func.get("arguments", "{}")
        call_id = call.get("id", "unknown")

        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            trace.trace_tool_error(tool_name, f"Invalid JSON arguments: {raw_args}")
            return {"error": f"Invalid JSON arguments: {raw_args}"}

        trace.trace_tool_call_start(tool_name, args)
        trace.trace_tool_lifecycle_start(tool_name, call_id, args)

        if tool_name == "resolve_contacts":
            blocked_result = self.controller._block_redundant_contact_resolution(state, args)
            if blocked_result is not None:
                return self._finalize_early_tool_result(
                    tool_name=tool_name,
                    args=args,
                    result=blocked_result,
                    state=state,
                    run_id=run_id,
                    call_id=call_id,
                    default_summary="Blocked redundant contact resolution",
                )

        if tool_name == "search_memories":
            args, preempt_result = self.controller._prepare_memory_search_arguments(
                args=args,
                state=state,
                question=question,
                user_email=user_email,
                conversation_history=conversation_history,
            )
            trace.trace_tool_args_normalized(tool_name, args)
            if preempt_result is not None:
                prompt = ""
                need_user_input = extract_need_user_input(
                    preempt_result,
                    default_source=tool_name,
                )
                if need_user_input:
                    prompt = str(need_user_input.get("prompt") or "").strip()
                if prompt:
                    state.add_question(prompt)
                return self._finalize_early_tool_result(
                    tool_name=tool_name,
                    args=args,
                    result=preempt_result,
                    state=state,
                    run_id=run_id,
                    call_id=call_id,
                    default_summary="Preempted for clarification",
                )

            blocked_search = self.controller._block_redundant_memory_search(state, args)
            if blocked_search is not None:
                return self._finalize_early_tool_result(
                    tool_name=tool_name,
                    args=args,
                    result=blocked_search,
                    state=state,
                    run_id=run_id,
                    call_id=call_id,
                    default_summary="Blocked redundant memory search",
                )

        if self.controller.config.enable_validation:
            trace.trace_pre_validation_start(tool_name)
            validation = self.controller.pre_validator.validate(tool_name, args)
            if not validation.valid:
                state.repair_count += 1
                trace.trace_pre_validation_fail(
                    tool_name,
                    validation.errors,
                    state.repair_count,
                )
                self.controller.logger.log_tool_call(
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

        step_start = perf_counter()
        result = self.controller._execute_handler(
            tool_name=tool_name,
            args=args,
            state=state,
            question=question,
            search_limit=search_limit,
            user_email=user_email,
            conversation_history=conversation_history,
        )
        duration_ms = (perf_counter() - step_start) * 1000

        if tool_name == "resolve_contacts":
            self.controller._update_contact_resolution_state(state, args, result)
        elif tool_name == "search_memories":
            self._track_information_candidates_from_search(state, args, result)
        elif tool_name == "get_document":
            self._mark_document_candidate_inspected(state, args, result)
        elif tool_name == "get_events":
            self._mark_event_candidates_inspected(state, result)
        elif tool_name == "lookup_contact":
            self._track_contact_candidates(state, result)

        success = "error" not in result and result.get("success") is not False
        result_summary = self._summarize_result(result, success=success)
        trace.trace_tool_execution_result(tool_name, duration_ms, success, result_summary)
        trace.trace_tool_lifecycle_end(tool_name, call_id, success, duration_ms, result_summary)
        if not success:
            trace.trace_tool_error(tool_name, result.get("error", "Unknown error"))

        record = ToolCallRecord(
            tool_name=tool_name,
            arguments=args,
            result=result,
            duration_ms=duration_ms,
            success=success,
            error=result.get("error"),
        )
        state.record_tool_call(record)
        self.controller.logger.log_tool_call(
            run_id,
            state.step_count,
            tool_name,
            args,
            duration_ms=duration_ms,
            result=result,
        )

        if self.controller.config.enable_validation:
            from tools.validators.post_execution import GoalCoverage

            trace.trace_post_validation_start(tool_name)
            post_result = self.controller.post_validator.validate(
                tool_name,
                args,
                result,
                state.goal,
                state.known_facts,
            )

            trace.trace_post_validation_result(
                tool_name,
                coverage=post_result.coverage.value if post_result.coverage else "unknown",
                passed=post_result.coverage != GoalCoverage.FAILED,
                reason=post_result.reason,
                suggested_tools=post_result.suggested_next_tools,
            )
            self.controller.logger.log_validation_result(
                validation_type="Post-execution",
                passed=post_result.coverage != GoalCoverage.FAILED,
                coverage=post_result.coverage.value if post_result.coverage else None,
                reason=post_result.reason,
                suggested_tools=post_result.suggested_next_tools,
            )

            for fact in post_result.extracted_facts:
                state.add_fact(fact)
                trace.trace_fact_extracted(fact)
                self.controller.logger.log_state_update("Fact added", fact)

            if post_result.coverage == GoalCoverage.FAILED:
                guidance = self.get_failure_guidance(tool_name, result, post_result)
                self.controller.logger.log_decision(
                    decision="Tool call failed - injecting recovery guidance",
                    reason=post_result.reason,
                    details={
                        "guidance": guidance[:100] + "..." if len(guidance) > 100 else guidance
                    },
                )
                result["_validation"] = {
                    "status": "failed",
                    "reason": post_result.reason,
                    "guidance": guidance,
                }
                if post_result.suggested_next_tools:
                    result["_validation"]["suggested_tools"] = post_result.suggested_next_tools
                state.add_fact(f"Tool {tool_name} failed: {post_result.reason}")
            elif post_result.coverage == GoalCoverage.NEED_USER_INPUT:
                clarification_prompt = post_result.reason.strip()
                if clarification_prompt:
                    state.add_question(clarification_prompt)
                    state.add_fact("User clarification required before continuing")
                    result.setdefault("_validation", {})
                    result["_validation"]["status"] = "need_user_input"
                    result["_validation"]["reason"] = clarification_prompt

        if success:
            evidence = self.get_completion_evidence(tool_name, args, result)
            if evidence:
                state.add_completion_evidence(evidence)

        return result

    def execute_handler(
        self,
        tool_name: str,
        args: dict[str, Any],
        state: AgentState,
        question: str,
        search_limit: int,
        user_email: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
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
                user_email=user_email,
                conversation_history=conversation_history,
            )
        except Exception as e:
            logger.exception("[controller] Tool execution error: %s", e)
            return {"error": str(e)}

    def get_completion_evidence(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> str | None:
        """Generate completion evidence string for successful tool execution."""
        if tool_name == "home_assistant":
            action = args.get("action")
            if action == "list_tools":
                return None
            if action == "call_tool":
                return f"Executed HA tool: {args.get('tool_name', 'unknown')}"

        if tool_name in ("search_memories", "get_events", "web_search"):
            count = result.get("count", 0)
            if count > 0:
                return f"{tool_name}: found {count} results"
            rows = result.get("rows") or result.get("results") or result.get("events", [])
            if rows:
                return f"{tool_name}: retrieved {len(rows)} items"
            return None

        if tool_name == "get_document":
            doc = result.get("document")
            if doc:
                return f"Retrieved document: {doc.get('title', 'untitled')}"
            return None

        if tool_name == "resolve_query":
            contacts = len(result.get("contacts", []))
            places = len(result.get("places", []))
            if contacts or places:
                return f"Resolved {contacts} contacts, {places} places"
            return None

        if tool_name == "resolve_contacts":
            status = result.get("status")
            resolved = len(result.get("resolved_contacts", []))
            if status == "success" and resolved > 0:
                return f"Resolved {resolved} contacts from natural language"
            return None

        return f"Executed {tool_name}"

    def get_failure_guidance(
        self,
        tool_name: str,
        result: dict[str, Any],
        post_result: Any,
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

    def _finalize_early_tool_result(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        state: AgentState,
        run_id: str,
        call_id: str,
        default_summary: str,
    ) -> dict[str, Any]:
        """Finalize an early-return tool result while preserving bookkeeping."""
        duration_ms = 0.0
        success = "error" not in result and result.get("success") is not False
        need_user_input = extract_need_user_input(result, default_source=tool_name)
        result_summary = (
            result.get("message")
            or (need_user_input or {}).get("prompt")
            or default_summary
        )
        trace.trace_tool_execution_result(tool_name, duration_ms, success, result_summary)
        trace.trace_tool_lifecycle_end(tool_name, call_id, success, duration_ms, result_summary)

        record = ToolCallRecord(
            tool_name=tool_name,
            arguments=args,
            result=result,
            duration_ms=duration_ms,
            success=success,
            error=result.get("error"),
        )
        state.record_tool_call(record)
        self.controller.logger.log_tool_call(
            run_id,
            state.step_count,
            tool_name,
            args,
            duration_ms=duration_ms,
            result=result,
        )
        return result

    def _summarize_result(self, result: dict[str, Any], success: bool) -> str:
        """Build a short, stable summary used in tracing."""
        if result.get("error"):
            return f"Error: {result.get('error')}"
        if "count" in result:
            return f"{result['count']} items"
        if "tools" in result:
            return f"Listed {len(result['tools'])} tools"
        if "rows" in result:
            return f"{len(result['rows'])} rows"
        if "results" in result:
            return f"{len(result['results'])} results"
        return "OK" if success else "Failed"

    def build_tool_message_payload(
        self,
        tool_name: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a compact tool payload for LLM context to limit token bloat."""
        if not isinstance(result, dict):
            return {"result": str(result)}

        if tool_name == "search_memories":
            return self._compact_search_memories_result(result)
        if tool_name == "get_document":
            return self._compact_get_document_result(result)
        if tool_name == "get_events":
            return self._compact_get_events_result(result)

        return result

    def _compact_search_memories_result(self, result: dict[str, Any]) -> dict[str, Any]:
        rows = result.get("results", [])
        if not isinstance(rows, list):
            return result

        compact_rows: list[dict[str, Any]] = []
        for row in rows[:10]:
            if not isinstance(row, dict):
                continue
            compact_row: dict[str, Any] = {
                "id": row.get("id"),
                "kind": row.get("kind"),
                "title": row.get("title"),
                "score": row.get("score"),
                "document_date": row.get("document_date"),
                "start_date": row.get("start_date"),
                "end_date": row.get("end_date"),
                "tags": row.get("tags"),
                "types": row.get("types"),
            }
            snippet = self._truncate_text(row.get("snippet"), 280)
            summary = self._truncate_text(row.get("summary"), 280)
            if snippet:
                compact_row["snippet"] = snippet
            if summary:
                compact_row["summary"] = summary
            compact_rows.append(compact_row)
        return {"results": compact_rows, "count": int(result.get("count", len(rows)) or 0)}

    def _compact_get_document_result(self, result: dict[str, Any]) -> dict[str, Any]:
        document = result.get("document")
        if not isinstance(document, dict):
            return result

        raw_metadata = document.get("raw_metadata") if isinstance(document.get("raw_metadata"), dict) else {}
        preview_source = (
            document.get("content_preview")
            or raw_metadata.get("content_english_for_embedding")
            or raw_metadata.get("original_content")
            or ""
        )

        compact_document: dict[str, Any] = {
            "document_id": document.get("document_id"),
            "title": document.get("title"),
            "tags": document.get("tags"),
            "document_date": document.get("document_date"),
            "file_name": document.get("file_name"),
            "file_mime": document.get("file_mime"),
            "file_size": document.get("file_size"),
            "snippet": self._truncate_text(document.get("snippet"), 280),
            "content_preview": self._truncate_text(preview_source, 12000),
        }
        return {"document": compact_document}

    def _compact_get_events_result(self, result: dict[str, Any]) -> dict[str, Any]:
        events = result.get("events", [])
        if not isinstance(events, list):
            return result

        compact_events: list[dict[str, Any]] = []
        for event in events[:10]:
            if not isinstance(event, dict):
                continue
            compact_event = {
                "id": event.get("id") or event.get("event_id"),
                "title": event.get("title"),
                "start_date": event.get("start_date"),
                "end_date": event.get("end_date"),
                "summary": self._truncate_text(event.get("summary"), 280),
                "tags": event.get("tags"),
                "types": event.get("types"),
            }
            compact_events.append(compact_event)
        return {"events": compact_events, "count": int(result.get("count", len(events)) or 0)}

    def _track_information_candidates_from_search(
        self,
        state: AgentState,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Persist top high-signal hits from search_memories for later reuse."""
        rows = result.get("results", [])
        if not isinstance(rows, list):
            return

        query = str(args.get("query") or "").strip()
        ranked_rows = sorted(
            [row for row in rows if isinstance(row, dict)],
            key=lambda item: self._safe_score(item.get("score")),
            reverse=True,
        )
        for row in ranked_rows[:5]:
            candidate_id = str(row.get("id") or "").strip()
            if not candidate_id:
                continue
            kind = self._infer_candidate_kind(row, candidate_id)
            label = (
                str(row.get("title") or "").strip()
                or self._truncate_text(row.get("summary"), 80)
                or self._truncate_text(row.get("snippet"), 80)
                or candidate_id
            )
            state.remember_information_candidate(
                kind=kind,
                candidate_id=candidate_id,
                label=label,
                score=row.get("score"),
                query=query,
                source_tool="search_memories",
            )

    def _mark_document_candidate_inspected(
        self,
        state: AgentState,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Mark a document candidate as inspected after `get_document` succeeds."""
        document = result.get("document") if isinstance(result.get("document"), dict) else {}
        document_id = (
            str(document.get("document_id") or "").strip()
            or str(args.get("document_id") or "").strip()
        )
        if not document_id:
            return

        label = str(document.get("title") or "").strip() or document_id
        state.mark_information_candidate_inspected(
            kind="document",
            candidate_id=document_id,
            label=label,
        )

    def _mark_event_candidates_inspected(
        self,
        state: AgentState,
        result: dict[str, Any],
    ) -> None:
        """Mark event candidates as inspected after `get_events` succeeds."""
        events = result.get("events", [])
        if not isinstance(events, list):
            return
        for event in events:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or event.get("event_id") or "").strip()
            if not event_id:
                continue
            title = str(event.get("title") or "").strip() or event_id
            state.mark_information_candidate_inspected(
                kind="event",
                candidate_id=event_id,
                label=title,
            )

    def _track_contact_candidates(
        self,
        state: AgentState,
        result: dict[str, Any],
    ) -> None:
        """Track surfaced contacts from lookup contact style tools."""
        contacts = result.get("contacts", [])
        if not isinstance(contacts, list):
            return
        for contact in contacts[:5]:
            if not isinstance(contact, dict):
                continue
            contact_id = str(contact.get("contact_id") or "").strip()
            if not contact_id:
                continue
            label = str(contact.get("display_name") or "").strip() or contact_id
            state.remember_information_candidate(
                kind="contact",
                candidate_id=contact_id,
                label=label,
                source_tool="lookup_contact",
            )

    def _infer_candidate_kind(self, row: dict[str, Any], candidate_id: str) -> str:
        kind = str(row.get("kind") or "").strip().lower()
        if kind:
            return kind
        prefix = candidate_id.split(":", 1)[0].strip().lower()
        if prefix:
            return prefix
        return "unknown"

    def _safe_score(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return -1.0

    def _truncate_text(self, value: Any, max_chars: int) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."
