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

import requests

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
        if tool_name in ("search_memories", "web_search", "execute_sql", "get_events"):
            results_key = "results" if tool_name != "execute_sql" else "rows"
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

        # Check describe_schema - always successful if no error
        if tool_name == "describe_schema" and "schema" in result:
            return PostExecutionResult(
                coverage=GoalCoverage.NEEDS_MORE_TOOLS,
                reason="Schema retrieved, ready for SQL queries",
                extracted_facts=["Database schema available"],
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
        headers = {"Content-Type": "application/json"}
        if self.llm_api_key:
            headers["Authorization"] = f"Bearer {self.llm_api_key}"

        payload = {
            "model": self.llm_model,
            "messages": [{"role": "user", "content": prompt}],
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
        return data.get("choices", [{}])[0].get("message", {}).get("content", "")

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

        elif tool_name == "execute_sql":
            rows = result.get("rows", [])
            if rows:
                facts.append(f"Query returned {len(rows)} rows")

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
            elif result.get("success"):
                facts.append("Home Assistant command executed successfully")

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
