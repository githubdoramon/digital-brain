"""
Post-execution validation for goal coverage.

Determines whether the agent should continue after tool execution:
1. Deterministic checks first (exit codes, empty results, etc.)
2. LLM-based result validator for ambiguous cases

This is the key component that decides:
- satisfied: Goal achieved, return answer
- needs_more_tools: Continue with more tool calls
- need_user_input: Ask user for clarification
- failed: Tool failed, handle error
"""

import json
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.router import TOOL_GROUPS
from observability import trace


class GoalCoverage(str, Enum):
    """Result of goal coverage check."""

    SATISFIED = "satisfied"
    NEEDS_MORE_TOOLS = "needs_more_tools"
    NEED_USER_INPUT = "need_user_input"
    FAILED = "failed"


@dataclass
class PostExecutionResult:
    """Result of post-execution validation."""

    coverage: GoalCoverage
    reason: str
    extracted_facts: list[str] = field(default_factory=list)
    suggested_next_tools: list[str] = field(default_factory=list)
    confidence: float = 1.0
    was_llm_validated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "coverage": self.coverage.value,
            "reason": self.reason,
            "extracted_facts": self.extracted_facts,
            "suggested_next_tools": self.suggested_next_tools,
            "confidence": self.confidence,
            "was_llm_validated": self.was_llm_validated,
        }


class PostExecutionValidator:
    """
    Validates tool results for goal coverage.

    Uses deterministic checks first for speed, then falls back to
    LLM validation for ambiguous cases.
    """

    def __init__(
        self,
        llm_base_url: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        llm_timeout: int = 15,
        enable_llm_validation: bool = True,
    ):
        """
        Initialize the validator.

        Args:
            llm_base_url: Base URL for LLM API (defaults to POST_VALIDATOR_BASE_URL or LLM_BASE_URL)
            llm_model: Model to use (defaults to POST_VALIDATOR_MODEL or LLM_CHAT_MODEL)
            llm_api_key: API key (defaults to POST_VALIDATOR_API_KEY or LLM_API_KEY)
            llm_timeout: Timeout for LLM calls
            enable_llm_validation: Whether to use LLM for ambiguous cases
        """
        self.llm_base_url = llm_base_url or os.getenv(
            "POST_VALIDATOR_BASE_URL", os.getenv("LLM_BASE_URL", "")
        )
        self.llm_model = llm_model or os.getenv(
            "POST_VALIDATOR_MODEL", os.getenv("LLM_CHAT_MODEL", "")
        )
        self.llm_api_key = llm_api_key or os.getenv(
            "POST_VALIDATOR_API_KEY", os.getenv("LLM_API_KEY", "")
        )
        self.llm_timeout = int(
            os.getenv("POST_VALIDATOR_TIMEOUT", str(llm_timeout))
        )
        self.enable_llm_validation = enable_llm_validation

    def validate(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: dict[str, Any],
        goal: str,
        known_facts: list[str],
        completed_actions: list[str] = None,
    ) -> PostExecutionResult:
        """
        Check if the tool result satisfies the goal.

        Args:
            tool_name: Name of the executed tool
            params: Parameters used
            result: Tool execution result
            goal: The user's original goal
            known_facts: Facts accumulated so far
            completed_actions: Actions completed so far

        Returns:
            PostExecutionResult with coverage status and extracted info
        """
        # Phase 1: Deterministic checks
        det_result = self._deterministic_check(tool_name, params, result)
        if det_result:
            return det_result

        # Phase 2: LLM-based check for ambiguous cases
        if self.enable_llm_validation and self.llm_base_url and self.llm_model:
            return self._llm_check(
                tool_name, params, result, goal, known_facts, completed_actions
            )

        # Default: needs more tools (let the main agent loop decide)
        return PostExecutionResult(
            coverage=GoalCoverage.NEEDS_MORE_TOOLS,
            reason="Result received, continuing to process",
            extracted_facts=self._extract_facts_from_result(tool_name, result),
        )

    def _deterministic_check(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: dict[str, Any],
    ) -> Optional[PostExecutionResult]:
        """
        Run deterministic validation checks.

        Returns PostExecutionResult if a definitive conclusion can be made,
        None if LLM validation is needed.
        """
        # Check for explicit error
        if "error" in result:
            error_msg = result["error"]
            return PostExecutionResult(
                coverage=GoalCoverage.FAILED,
                reason=f"Tool error: {error_msg}",
                suggested_next_tools=self._suggest_alternative_tools(tool_name),
            )

        # Check for success: False (some tools like home_assistant use this pattern)
        if result.get("success") is False:
            error_msg = result.get("error", "Operation failed")
            return PostExecutionResult(
                coverage=GoalCoverage.FAILED,
                reason=f"Tool returned failure: {error_msg}",
                suggested_next_tools=self._suggest_alternative_tools(tool_name),
            )

        # Check return codes (for bash, scripts, etc.)
        if "returncode" in result and result["returncode"] != 0:
            return PostExecutionResult(
                coverage=GoalCoverage.FAILED,
                reason=f"Non-zero exit code: {result['returncode']}",
                extracted_facts=[f"Command failed: {result.get('stderr', 'unknown error')}"],
            )

        # Check for empty results from search/query tools
        if tool_name in ("search_memories", "web_search", "get_events"):
            if tool_name == "search_memories" and result.get("needs_clarification"):
                return PostExecutionResult(
                    coverage=GoalCoverage.NEED_USER_INPUT,
                    reason=result.get(
                        "clarification_prompt",
                        "Multiple people matched. User clarification is required.",
                    ),
                    extracted_facts=["Contact disambiguation required before memory search"],
                )

            results_key = "results"
            if tool_name == "get_events":
                results_key = "events"

            results = result.get(results_key, result.get("results", []))
            if isinstance(results, list) and len(results) == 0:
                return PostExecutionResult(
                    coverage=GoalCoverage.NEEDS_MORE_TOOLS,
                    reason=f"No results from {tool_name}, may need different approach",
                    suggested_next_tools=self._suggest_alternative_tools(tool_name),
                )

            # Non-empty results - extract facts
            if results:
                facts = self._extract_facts_from_result(tool_name, result)
                return PostExecutionResult(
                    coverage=GoalCoverage.NEEDS_MORE_TOOLS,
                    reason=f"Got {len(results)} results, evaluating",
                    extracted_facts=facts,
                )

        # Check resolve_query - extract entity info
        if tool_name == "resolve_query":
            contacts = result.get("contacts", [])
            places = result.get("places", [])
            facts = []
            if contacts:
                facts.append(f"Resolved {len(contacts)} contacts")
            if places:
                facts.append(f"Resolved {len(places)} places")

            return PostExecutionResult(
                coverage=GoalCoverage.NEEDS_MORE_TOOLS,
                reason="Entities resolved, ready for queries",
                extracted_facts=facts,
                )

        # Check resolve_contacts - extract resolution status and ambiguity signals
        if tool_name == "resolve_contacts":
            status = result.get("status", "unknown")
            people = result.get("people_mentioned", [])
            resolved = result.get("resolved_contacts", [])
            ambiguous = result.get("ambiguous_contacts", [])

            facts = []
            if people:
                facts.append(f"Detected {len(people)} person mentions")
            if resolved:
                facts.append(f"Resolved {len(resolved)} contacts")

            if status == "needs_clarification" or ambiguous:
                prompt = ""
                if ambiguous:
                    prompt = ambiguous[0].get("clarification_prompt", "")
                return PostExecutionResult(
                    coverage=GoalCoverage.NEED_USER_INPUT,
                    reason=prompt or "Contact resolution requires user clarification",
                    extracted_facts=facts or ["Ambiguous contact resolution"],
                )

            if status == "no_people":
                return PostExecutionResult(
                    coverage=GoalCoverage.NEEDS_MORE_TOOLS,
                    reason="No people detected; continue without contact filters",
                    extracted_facts=facts,
                    suggested_next_tools=["search_memories", "resolve_query"],
                )

            if status == "success":
                return PostExecutionResult(
                    coverage=GoalCoverage.NEEDS_MORE_TOOLS,
                    reason="Contacts resolved and ready for downstream queries",
                    extracted_facts=facts,
                    suggested_next_tools=["search_memories", "lookup_contact"],
                )

            return PostExecutionResult(
                coverage=GoalCoverage.FAILED,
                reason=result.get("message", "Contact resolution failed"),
                suggested_next_tools=["resolve_query", "search_memories"],
            )

        # Check lookup_contact - extract contact search/relationship results
        if tool_name == "lookup_contact":
            action = params.get("action", "search")
            facts = []

            if result.get("error"):
                return PostExecutionResult(
                    coverage=GoalCoverage.FAILED,
                    reason=f"Contact lookup failed: {result['error']}",
                    suggested_next_tools=["resolve_query", "search_memories"],
                )

            if action == "search":
                count = result.get("count", 0)
                contacts = result.get("contacts", [])
                if contacts:
                    names = [c.get("display_name", "Unknown") for c in contacts[:3]]
                    facts.append(f"Found {count} contacts: {', '.join(names)}")
                    return PostExecutionResult(
                        coverage=GoalCoverage.NEEDS_MORE_TOOLS,
                        reason=f"Found {count} matching contacts",
                        extracted_facts=facts,
                    )
                else:
                    return PostExecutionResult(
                        coverage=GoalCoverage.NEEDS_MORE_TOOLS,
                        reason="No contacts found, may need different search",
                        suggested_next_tools=["resolve_query", "search_memories"],
                    )

            elif action in ("get_relationships", "find_related"):
                if result.get("found"):
                    rel_count = result.get("relationship_count", 0)
                    contact_name = result.get("primary_contact", result.get("contact", {})).get("display_name", "Unknown")
                    facts.append(f"Found {rel_count} relationships for {contact_name}")

                    # If we found relationships, this might be the final answer
                    if rel_count > 0:
                        related = result.get("related_contacts", result.get("relationships", []))
                        if related:
                            rel_names = [
                                r.get("related_contact", {}).get("display_name", "Unknown")
                                for r in related[:3]
                            ]
                            facts.append(f"Related contacts: {', '.join(rel_names)}")

                    return PostExecutionResult(
                        coverage=GoalCoverage.NEEDS_MORE_TOOLS,
                        reason=f"Retrieved relationships for {contact_name}",
                        extracted_facts=facts,
                    )
                else:
                    return PostExecutionResult(
                        coverage=GoalCoverage.NEEDS_MORE_TOOLS,
                        reason="Contact not found for relationship lookup",
                        suggested_next_tools=["lookup_contact", "resolve_query"],
                    )

        # For other tools, return None to trigger LLM check
        return None

    def _llm_check(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: dict[str, Any],
        goal: str,
        known_facts: list[str],
        completed_actions: list[str] = None,
    ) -> PostExecutionResult:
        """
        Use LLM to assess goal coverage for ambiguous results.

        This is called when deterministic checks can't make a decision.
        """
        prompt = self._build_validation_prompt(
            tool_name, params, result, goal, known_facts, completed_actions
        )

        try:
            response = self._call_llm(prompt)
            return self._parse_llm_response(response)
        except Exception as e:
            trace.trace_tool_error("post_validator", f"LLM check failed: {e}")
            # Default to needs_more_tools on failure
            return PostExecutionResult(
                coverage=GoalCoverage.NEEDS_MORE_TOOLS,
                reason="Validation inconclusive, continuing",
                extracted_facts=self._extract_facts_from_result(tool_name, result),
            )

    def _build_validation_prompt(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: dict[str, Any],
        goal: str,
        known_facts: list[str],
        completed_actions: list[str] = None,
    ) -> str:
        """Build the prompt for LLM validation."""
        # Truncate result if too large
        result_str = json.dumps(result, default=str)
        if len(result_str) > 2000:
            result_str = result_str[:2000] + "... (truncated)"

        facts_str = "\n".join(f"- {f}" for f in known_facts) if known_facts else "None"
        actions_str = (
            "\n".join(f"- {a}" for a in (completed_actions or []))
            if completed_actions
            else "None"
        )

        return f"""You are a goal coverage validator. Analyze if the tool result helps satisfy the user's goal.

USER GOAL: {goal}

KNOWN FACTS:
{facts_str}

COMPLETED ACTIONS:
{actions_str}

TOOL EXECUTED: {tool_name}
PARAMETERS: {json.dumps(params, default=str)}
RESULT: {result_str}

Respond with JSON only:
{{
  "status": "satisfied | needs_more_tools | need_user_input | failed",
  "reason": "brief explanation",
  "extracted_facts": ["fact1", "fact2"],
  "suggested_next_tools": ["tool1", "tool2"] or []
}}

Rules:
- "satisfied": The goal can now be fully answered with available information
- "needs_more_tools": More data is needed, suggest which tools to use
- "need_user_input": Clarification required from user
- "failed": The tool failed or returned unusable data"""

    def _call_llm(self, prompt: str) -> str:
        """Make LLM API call for validation."""
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from llm_helpers import call_llm

        return call_llm(prompt, timeout=self.llm_timeout)

    def _parse_llm_response(self, response: str) -> PostExecutionResult:
        """Parse LLM response into PostExecutionResult."""
        try:
            # Try to extract JSON from response
            response = response.strip()
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]

            data = json.loads(response)

            status_map = {
                "satisfied": GoalCoverage.SATISFIED,
                "needs_more_tools": GoalCoverage.NEEDS_MORE_TOOLS,
                "need_user_input": GoalCoverage.NEED_USER_INPUT,
                "failed": GoalCoverage.FAILED,
            }

            coverage = status_map.get(
                data.get("status", "needs_more_tools"),
                GoalCoverage.NEEDS_MORE_TOOLS,
            )

            return PostExecutionResult(
                coverage=coverage,
                reason=data.get("reason", "LLM validation complete"),
                extracted_facts=data.get("extracted_facts", []),
                suggested_next_tools=data.get("suggested_next_tools", []),
                was_llm_validated=True,
            )

        except (json.JSONDecodeError, KeyError) as e:
            trace.trace_tool_error("post_validator", f"Failed to parse LLM response: {e}")
            return PostExecutionResult(
                coverage=GoalCoverage.NEEDS_MORE_TOOLS,
                reason="Validation response parsing failed",
                was_llm_validated=True,
            )

    def _extract_facts_from_result(
        self,
        tool_name: str,
        result: dict[str, Any],
    ) -> list[str]:
        """Extract useful facts from tool results."""
        facts = []

        if tool_name == "search_memories":
            count = result.get("count", len(result.get("results", [])))
            if count > 0:
                facts.append(f"Found {count} relevant memories")

        elif tool_name == "get_events":
            events = result.get("events", [])
            if events:
                facts.append(f"Retrieved {len(events)} event details")

        elif tool_name == "get_document":
            doc = result.get("document")
            if doc:
                facts.append(f"Retrieved document: {doc.get('title', 'untitled')}")

        elif tool_name == "web_search":
            results = result.get("results", [])
            if results:
                facts.append(f"Web search returned {len(results)} results")

        elif tool_name == "home_assistant":
            if result.get("tools"):
                facts.append(f"Listed {len(result['tools'])} Home Assistant tools")
                # This is a discovery step - the goal is NOT yet achieved
                facts.append("PENDING: Need to call the actual HA tool to complete the action")
            elif result.get("success"):
                facts.append("Home Assistant command executed successfully")
                # This is the completion step - mark as achieved
                facts.append("GOAL_ACHIEVED: Device command completed")

        elif tool_name == "lookup_contact":
            action = result.get("action", "search")
            if action == "search":
                count = result.get("count", 0)
                if count > 0:
                    contacts = result.get("contacts", [])
                    names = [c.get("display_name", "Unknown") for c in contacts[:3]]
                    facts.append(f"Found {count} contacts: {', '.join(names)}")
            elif action in ("get_relationships", "find_related"):
                if result.get("found"):
                    rel_count = result.get("relationship_count", 0)
                    contact_name = result.get("primary_contact", result.get("contact", {})).get("display_name", "Unknown")
                    facts.append(f"Found {rel_count} relationships for {contact_name}")

        elif tool_name == "resolve_contacts":
            status = result.get("status", "unknown")
            resolved = result.get("resolved_contacts", [])
            ambiguous = result.get("ambiguous_contacts", [])
            if status == "success" and resolved:
                facts.append(f"Resolved {len(resolved)} contacts from text")
            elif status == "needs_clarification" or ambiguous:
                facts.append("Contact resolution is ambiguous and needs clarification")

        return facts

    def _suggest_alternative_tools(self, failed_tool: str) -> list[str]:
        """
        Suggest alternative tools when one returns no results.

        Dynamically finds tools in the same group(s) as the failed tool,
        excluding the failed tool itself.
        """
        alternatives = []

        # Find which groups contain the failed tool
        for _group_name, tools in TOOL_GROUPS.items():
            if failed_tool in tools:
                # Add other tools from the same group
                for tool in tools:
                    if tool != failed_tool and tool not in alternatives:
                        alternatives.append(tool)

        return alternatives


class GoalCompletionValidator:
    """
    Validates that the user's actual goal was achieved.

    This is separate from tool execution validation - it checks whether
    the entire task is complete, not just individual tool calls.

    Inspired by clawdbot's result aggregation pattern.

    The validator is generic and works across all tool types by:
    1. Detecting goal type (action vs query vs conversational)
    2. Checking for discovery-only patterns (list/describe but no execute)
    3. Verifying actual results exist
    """

    # Patterns that indicate an ACTION goal (user wants something done)
    ACTION_PATTERNS = [
        # Device/IoT actions
        "turn on", "turn off", "switch on", "switch off",
        "dim", "brighten", "set", "change", "adjust",
        "open", "close", "lock", "unlock",
        "start", "stop", "pause", "resume",
        # Data actions
        "create", "add", "insert", "save", "store",
        "delete", "remove", "update", "modify", "edit",
        "send", "execute", "run", "trigger",
    ]

    # Patterns that indicate a QUERY goal (user wants information)
    QUERY_PATTERNS = [
        "what", "who", "when", "where", "why", "how",
        "find", "search", "list", "show", "tell me",
        "do i have", "is there", "are there",
        "get", "retrieve", "fetch", "look up", "lookup",
    ]

    # Tool-specific discovery actions that need follow-up execution
    # Format: {tool_name: {discovery_action: execution_action}}
    DISCOVERY_EXECUTION_PAIRS = {
        "home_assistant": {
            "list_tools": "call_tool",
        },
    }

    def check_goal_achieved(
        self,
        goal: str,
        tool_calls: list,
        known_facts: list[str],
        final_content: str,
    ) -> tuple[bool, str, list[str]]:
        """
        Check if the user's goal was actually achieved.

        Args:
            goal: The original user goal
            tool_calls: List of ToolCallRecord objects
            known_facts: Facts accumulated during execution
            final_content: The final response content

        Returns:
            Tuple of (achieved: bool, reason: str, pending_actions: list[str])
        """
        goal_lower = goal.lower()

        # Determine goal type
        is_action_goal = any(p in goal_lower for p in self.ACTION_PATTERNS)
        is_query_goal = any(p in goal_lower for p in self.QUERY_PATTERNS)

        # Check for discovery-only pattern (generic across all tools)
        discovery_check = self._check_discovery_without_execution(tool_calls, known_facts)
        if discovery_check[0] is False and discovery_check[2]:
            # Discovery was done but execution is pending
            return discovery_check

        # For action goals, check for actual execution
        if is_action_goal:
            return self._check_action_goal_achieved(goal, tool_calls, known_facts)

        # For query goals, check if we have a substantive answer
        if is_query_goal:
            return self._check_query_goal_achieved(goal, tool_calls, known_facts, final_content)

        # Default: check for any successful tool execution or substantive content
        successful_calls = [tc for tc in tool_calls if tc.success]
        if successful_calls:
            return (True, "Tools executed successfully", [])

        # No tool calls but has content - might be conversational
        if final_content and len(final_content.strip()) > 20:
            return (True, "Response generated", [])

        return (False, "No successful tool execution detected", [])

    def _check_discovery_without_execution(
        self,
        tool_calls: list,
        known_facts: list[str],
    ) -> tuple[bool, str, list[str]]:
        """
        Check if we did discovery (list/describe) but not execution.

        This is a generic check that works across all tools.
        """
        if not tool_calls:
            return (True, "No tool calls", [])  # Not a discovery issue

        # Check for PENDING markers in facts
        pending_facts = [f for f in known_facts if f.startswith("PENDING:")]
        if pending_facts:
            # Extract the pending action from the fact
            pending_action = pending_facts[-1].replace("PENDING:", "").strip()
            return (False, "Discovery complete but action pending", [pending_action])

        # Check tool-specific discovery patterns
        tool_names = {tc.tool_name for tc in tool_calls}

        for tool_name, pairs in self.DISCOVERY_EXECUTION_PAIRS.items():
            for discovery_action, execution_action in pairs.items():
                if discovery_action is None:
                    # The tool itself is discovery
                    if tool_name in tool_names:
                        # Check if execution tool was also called
                        if execution_action not in tool_names:
                            has_successful_discovery = any(
                                tc.tool_name == tool_name and tc.success
                                for tc in tool_calls
                            )
                            if has_successful_discovery:
                                return (
                                    False,
                                    f"Used {tool_name} for discovery but did not execute",
                                    [f"Use {execution_action} to complete the action"]
                                )
                else:
                    # Check action parameter (like home_assistant's action param)
                    discovery_calls = [
                        tc for tc in tool_calls
                        if tc.tool_name == tool_name
                        and tc.arguments.get("action") == discovery_action
                        and tc.success
                    ]
                    execution_calls = [
                        tc for tc in tool_calls
                        if tc.tool_name == tool_name
                        and tc.arguments.get("action") == execution_action
                    ]

                    if discovery_calls and not execution_calls:
                        return (
                            False,
                            f"Used {tool_name} {discovery_action} but did not {execution_action}",
                            [f"Call {tool_name} with action='{execution_action}' to complete"]
                        )

        return (True, "No discovery-only pattern detected", [])

    def _check_action_goal_achieved(
        self,
        goal: str,
        tool_calls: list,
        known_facts: list[str],
    ) -> tuple[bool, str, list[str]]:
        """Check if an action goal was achieved (e.g., turn off lights, create event)."""

        if not tool_calls:
            return (False, "No tool calls made for action", ["Execute the required tool"])

        # Check for GOAL_ACHIEVED marker in facts
        goal_achieved_facts = [
            f for f in known_facts
            if "GOAL_ACHIEVED" in f or "successfully" in f.lower() or "completed" in f.lower()
        ]
        if goal_achieved_facts:
            return (True, "Action completed successfully", [])

        # Check for successful tool calls that modify data
        successful_calls = [tc for tc in tool_calls if tc.success]

        # Look for execution-type calls (not just discovery)
        execution_calls = []
        for tc in successful_calls:
            # Skip pure discovery tools
            # Skip discovery actions
            if tc.tool_name in self.DISCOVERY_EXECUTION_PAIRS:
                pairs = self.DISCOVERY_EXECUTION_PAIRS[tc.tool_name]
                action = tc.arguments.get("action")
                if action in pairs:
                    continue  # This is a discovery action
            execution_calls.append(tc)

        if execution_calls:
            return (True, "Action executed", [])

        # Only discovery calls were made
        if successful_calls:
            return (
                False,
                "Only discovery/query tools were used, action not executed",
                ["Execute the tool that performs the actual action"]
            )

        # Failed calls
        failed_calls = [tc for tc in tool_calls if not tc.success]
        if failed_calls:
            last_error = failed_calls[-1].error or "unknown error"
            return (
                False,
                f"Action failed: {last_error}",
                ["Retry with corrected parameters"]
            )

        return (False, "No action execution detected", ["Execute the required action"])

    def _check_query_goal_achieved(
        self,
        goal: str,
        tool_calls: list,
        known_facts: list[str],
        final_content: str,
    ) -> tuple[bool, str, list[str]]:
        """Check if a query goal was achieved (e.g., search for memories)."""
        goal_lower = goal.lower()
        temporal_goal = any(
            phrase in goal_lower
            for phrase in (
                "most recent",
                "latest",
                "last time",
                "last meeting",
                "last event",
                "first time",
                "first meeting",
                "first event",
                "earliest",
                "when did i first",
                "when did i last",
            )
        )

        # For queries, we need actual results
        if not tool_calls:
            return (False, "No tool calls made for query", ["Search for relevant information"])

        # Check if we got actual results from facts
        result_indicators = ["found", "retrieved", "returned", "results", "rows", "items", "records"]
        result_facts = [
            f for f in known_facts
            if any(w in f.lower() for w in result_indicators)
        ]

        if result_facts:
            if temporal_goal:
                has_temporal_resolution = any(
                    t.success and t.tool_name == "get_events"
                    for t in tool_calls
                )
                if not has_temporal_resolution:
                    return (
                        False,
                        "Temporal query needs explicit date-ordered verification",
                        ["Retrieve ordered event details before final answer"],
                    )
            return (True, "Query returned results", [])

        # Check for successful query tools
        query_tools = [
            "search_memories",
            "get_events",
            "get_document",
            "web_search",
            "lookup_contact",
            "resolve_contacts",
        ]
        successful_query_calls = [
            tc for tc in tool_calls
            if tc.tool_name in query_tools and tc.success
        ]

        if successful_query_calls:
            # Check if results were actually returned
            for tc in successful_query_calls:
                result = tc.result
                if self._has_results(result):
                    if temporal_goal:
                        has_temporal_resolution = any(
                            t.success and t.tool_name == "get_events"
                            for t in tool_calls
                        )
                        if not has_temporal_resolution:
                            return (
                                False,
                                "Temporal query needs explicit date-ordered verification",
                                ["Retrieve ordered event details before final answer"],
                            )
                    return (True, "Query returned data", [])

            # Query succeeded but no results
            return (
                False,
                "Query executed but returned no results",
                ["Try different search terms or alternative tools"]
            )

        # Check if final content has substantive information
        if final_content and len(final_content.strip()) > 50:
            return (True, "Response contains substantive information", [])

        return (
            False,
            "Query did not return useful results",
            ["Try alternative search terms or tools"]
        )

    def _has_results(self, result: dict) -> bool:
        """Check if a tool result contains actual data."""
        if not result or not isinstance(result, dict):
            return False

        # Check common result containers
        for key in [
            "results",
            "rows",
            "events",
            "documents",
            "items",
            "data",
            "tools",
            "resolved_contacts",
        ]:
            value = result.get(key)
            if isinstance(value, list) and len(value) > 0:
                return True

        # Check count indicators
        if result.get("count", 0) > 0:
            return True

        # Check success with data
        if result.get("success") and not result.get("error"):
            # Has some non-error content
            return True

        return False
