"""
LLM-based intent router for task classification.

The intent router:
1. Classifies the user's intent
2. Restricts tool visibility (tool-set narrowing)
3. Provides skill hints for the skill matcher

This is a separate, dedicated LLM call at the start of each request.
It can use a smaller/faster model for efficiency.
"""

import json
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from time import perf_counter
from typing import Any, Optional

import requests

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from observability import trace


class IntentType(str, Enum):
    """Classified intent types."""

    MEMORY_SEARCH = "memory_search"  # Searching memories, events, documents
    DATA_QUERY = "data_query"  # SQL queries, schema exploration
    CONTACT_LOOKUP = "contact_lookup"  # People, relationships
    WEB_SEARCH = "web_search"  # External information
    HOME_CONTROL = "home_control"  # Home Assistant actions
    SKILL_EXECUTION = "skill_execution"  # Running skill scripts
    SYSTEM_COMMAND = "system_command"  # Bash commands
    CONVERSATIONAL = "conversational"  # General chat, no tools needed
    COMPLEX = "complex"  # Multi-step task requiring multiple tool groups
    UNKNOWN = "unknown"  # Fallback


@dataclass
class IntentClassification:
    """Result of intent classification."""

    intent: IntentType
    confidence: float
    allowed_tool_groups: list[str]
    constraints: list[str] = field(default_factory=list)
    skill_hints: list[str] = field(default_factory=list)
    reasoning: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "allowed_tool_groups": self.allowed_tool_groups,
            "constraints": self.constraints,
            "skill_hints": self.skill_hints,
            "reasoning": self.reasoning,
        }


# Tool group mappings
TOOL_GROUPS = {
    "memory": ["search_memories", "get_events", "get_document"],
    "database": ["execute_sql", "describe_schema"],
    "resolution": ["resolve_query"],
    "web": ["web_search"],
    "home": ["home_assistant"],
    "skills": ["run_skill_script"],
    "system": ["bash"],
}

# Intent to tool group mappings
INTENT_TOOL_MAP = {
    IntentType.MEMORY_SEARCH: ["memory", "resolution"],
    IntentType.DATA_QUERY: ["database", "resolution"],
    IntentType.CONTACT_LOOKUP: ["resolution", "database", "memory"],
    IntentType.WEB_SEARCH: ["web"],
    IntentType.HOME_CONTROL: ["home"],
    IntentType.SKILL_EXECUTION: ["skills", "memory"],
    IntentType.SYSTEM_COMMAND: ["system"],
    IntentType.CONVERSATIONAL: [],  # No tools
    IntentType.COMPLEX: list(TOOL_GROUPS.keys()),  # All tools
    IntentType.UNKNOWN: list(TOOL_GROUPS.keys()),  # All tools
}

# Intent to skill hints mapping
INTENT_SKILL_HINTS = {
    IntentType.MEMORY_SEARCH: ["document-search", "event-analysis"],
    IntentType.DATA_QUERY: [],
    IntentType.CONTACT_LOOKUP: ["contact-lookup"],
    IntentType.WEB_SEARCH: [],
    IntentType.HOME_CONTROL: ["homeassistant"],
    IntentType.SKILL_EXECUTION: [],
    IntentType.SYSTEM_COMMAND: [],
    IntentType.CONVERSATIONAL: [],
    IntentType.COMPLEX: [],
    IntentType.UNKNOWN: [],
}


class IntentRouter:
    """
    LLM-based intent router for task classification.

    Restricts tool visibility based on intent (tool-set narrowing).
    Uses a separate LLM call with potentially a smaller/faster model.
    """

    def __init__(
        self,
        llm_base_url: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        llm_timeout: int = 10,
        enable_llm_routing: bool = True,
    ):
        """
        Initialize the intent router.

        Args:
            llm_base_url: Base URL for LLM API (defaults to INTENT_ROUTER_BASE_URL or LLM_BASE_URL)
            llm_model: Model to use (defaults to INTENT_ROUTER_MODEL or LLM_CHAT_MODEL)
            llm_api_key: API key (defaults to INTENT_ROUTER_API_KEY or LLM_API_KEY)
            llm_timeout: Timeout for LLM calls (default 10s)
            enable_llm_routing: Whether to use LLM for classification (False = rule-based only)
        """
        self.llm_base_url = llm_base_url or os.getenv(
            "INTENT_ROUTER_BASE_URL", os.getenv("LLM_BASE_URL", "")
        )
        self.llm_model = llm_model or os.getenv(
            "INTENT_ROUTER_MODEL", os.getenv("LLM_CHAT_MODEL", "")
        )
        self.llm_api_key = llm_api_key or os.getenv(
            "INTENT_ROUTER_API_KEY", os.getenv("LLM_API_KEY", "")
        )
        self.llm_timeout = int(os.getenv("INTENT_ROUTER_TIMEOUT", str(llm_timeout)))
        self.enable_llm_routing = enable_llm_routing

    async def classify(
        self,
        question: str,
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> IntentClassification:
        """
        Classify the user's intent.

        Uses rule-based heuristics first, then LLM for ambiguous cases.

        Args:
            question: The user's question
            conversation_history: Optional conversation history for context

        Returns:
            IntentClassification with intent, tool groups, and constraints
        """
        start_time = trace.trace_router_start(question)

        # Try rule-based classification first
        rule_result = self._rule_based_classify(question)
        if rule_result and rule_result.confidence >= 0.8:
            duration_ms = (perf_counter() - start_time) * 1000
            trace.trace_router_rule_match(
                rule_result.intent.value,
                rule_result.confidence,
                rule_result.reasoning or "",
                rule_result.allowed_tool_groups,
                duration_ms,
            )
            return rule_result

        # Use LLM for ambiguous cases
        if self.enable_llm_routing and self.llm_base_url and self.llm_model:
            trace.trace_router_llm_start()
            try:
                llm_start = perf_counter()
                llm_result = self._llm_classify(question, conversation_history)
                llm_duration = (perf_counter() - llm_start) * 1000
                trace.trace_router_llm_result(
                    llm_result.intent.value,
                    llm_result.confidence,
                    llm_result.reasoning,
                    llm_result.allowed_tool_groups,
                    llm_duration,
                )
                return llm_result
            except Exception as e:
                trace.trace_router_llm_error(str(e))

        # Fallback to rule-based result or unknown
        if rule_result:
            trace.trace_router_fallback(rule_result.intent.value, "Using rule-based result")
            return rule_result

        trace.trace_router_fallback(IntentType.UNKNOWN.value, "No classification match")
        return IntentClassification(
            intent=IntentType.UNKNOWN,
            confidence=0.5,
            allowed_tool_groups=list(TOOL_GROUPS.keys()),
            reasoning="Fallback to all tools",
        )

    def _rule_based_classify(self, question: str) -> Optional[IntentClassification]:
        """
        Simple rule-based classification using keywords.

        Returns None if confidence is too low for definitive classification.
        """
        q_lower = question.lower()

        # Home control patterns
        home_keywords = [
            "turn on", "turn off", "switch", "light", "lamp",
            "thermostat", "temperature", "home assistant", "smart home",
            "alexa", "google home", "hvac", "ac", "heater", "office", "heater"
        ]
        if any(kw in q_lower for kw in home_keywords):
            return IntentClassification(
                intent=IntentType.HOME_CONTROL,
                confidence=0.9,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.HOME_CONTROL],
                skill_hints=INTENT_SKILL_HINTS[IntentType.HOME_CONTROL],
                reasoning="Home control keywords detected",
            )

        # Web search patterns
        web_keywords = [
            "search the web", "google", "look up online",
            "what is", "who is", "news about", "latest on",
            "current", "today's", "recent news",
        ]
        if any(kw in q_lower for kw in web_keywords):
            return IntentClassification(
                intent=IntentType.WEB_SEARCH,
                confidence=0.85,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.WEB_SEARCH],
                skill_hints=INTENT_SKILL_HINTS[IntentType.WEB_SEARCH],
                reasoning="Web search keywords detected",
            )

        # Contact/people patterns
        contact_keywords = [
            "who is", "contact", "phone number", "email address",
            "relationship", "friend", "family", "colleague",
            "birthday", "when was .* born",
        ]
        if any(kw in q_lower for kw in contact_keywords):
            return IntentClassification(
                intent=IntentType.CONTACT_LOOKUP,
                confidence=0.85,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.CONTACT_LOOKUP],
                skill_hints=INTENT_SKILL_HINTS[IntentType.CONTACT_LOOKUP],
                reasoning="Contact/people keywords detected",
            )

        # Memory search patterns
        memory_keywords = [
            "remember", "recall", "what happened", "when did",
            "find", "search", "look for", "meeting", "event",
            "document", "note", "last time", "history",
        ]
        if any(kw in q_lower for kw in memory_keywords):
            return IntentClassification(
                intent=IntentType.MEMORY_SEARCH,
                confidence=0.8,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.MEMORY_SEARCH],
                skill_hints=INTENT_SKILL_HINTS[IntentType.MEMORY_SEARCH],
                reasoning="Memory search keywords detected",
            )

        # SQL/data query patterns
        sql_keywords = [
            "how many", "count", "list all", "show all",
            "database", "query", "sql", "table",
            "aggregate", "sum", "average", "total",
        ]
        if any(kw in q_lower for kw in sql_keywords):
            return IntentClassification(
                intent=IntentType.DATA_QUERY,
                confidence=0.8,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.DATA_QUERY],
                skill_hints=INTENT_SKILL_HINTS[IntentType.DATA_QUERY],
                reasoning="Data query keywords detected",
            )

        # System command patterns
        system_keywords = [
            "run command", "execute", "bash", "shell",
            "curl", "script", "terminal",
        ]
        if any(kw in q_lower for kw in system_keywords):
            return IntentClassification(
                intent=IntentType.SYSTEM_COMMAND,
                confidence=0.85,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.SYSTEM_COMMAND],
                skill_hints=INTENT_SKILL_HINTS[IntentType.SYSTEM_COMMAND],
                reasoning="System command keywords detected",
            )

        # Conversational patterns (greetings, thanks, etc.)
        conversational_keywords = [
            "hello", "hi ", "hey ", "thanks", "thank you",
            "goodbye", "bye", "how are you", "good morning",
            "good night", "help", "what can you do",
        ]
        if any(kw in q_lower for kw in conversational_keywords):
            return IntentClassification(
                intent=IntentType.CONVERSATIONAL,
                confidence=0.9,
                allowed_tool_groups=[],
                skill_hints=[],
                reasoning="Conversational keywords detected",
            )

        # No clear match - return low confidence result
        return IntentClassification(
            intent=IntentType.UNKNOWN,
            confidence=0.4,
            allowed_tool_groups=list(TOOL_GROUPS.keys()),
            reasoning="No clear keyword match",
        )

    def _llm_classify(
        self,
        question: str,
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> IntentClassification:
        """Use LLM to classify the intent."""
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from llm_helpers import call_llm

        prompt = self._build_classification_prompt(question, conversation_history)

        try:
            content = call_llm(prompt, timeout=self.llm_timeout)
            return self._parse_llm_response(content)
        except Exception as e:
            trace.trace_router_llm_error(f"LLM call failed: {e}")
            # Fall back to unknown classification
            return IntentClassification(
                intent=IntentType.UNKNOWN,
                confidence=0.5,
                allowed_tool_groups=list(TOOL_GROUPS.keys()),
                reasoning=f"LLM call failed: {e}",
            )

    def _build_classification_prompt(
        self,
        question: str,
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> str:
        """Build the prompt for LLM classification."""
        context = ""
        if conversation_history:
            recent = conversation_history[-3:]  # Last 3 messages for context
            context = "\n".join(
                f"{msg['role']}: {msg['content'][:200]}" for msg in recent
            )
            context = f"\nRECENT CONTEXT:\n{context}\n"

        return f"""Classify the user's intent to determine which tools are needed.

QUESTION: {question}
{context}
INTENT TYPES:
- memory_search: Searching memories, events, documents
- data_query: SQL queries, counting, aggregation
- contact_lookup: Finding people, relationships
- web_search: External information from the internet
- home_control: Smart home/Home Assistant actions
- skill_execution: Running skill scripts
- system_command: Bash/shell commands
- conversational: General chat, no tools needed
- complex: Multi-step task needing multiple tool groups

Respond with JSON only:
{{
  "intent": "one of the intent types above",
  "confidence": 0.0 to 1.0,
  "constraints": ["read_only"] or [],
  "skill_hints": ["relevant-skill-names"] or [],
  "reasoning": "brief explanation"
}}"""

    def _parse_llm_response(self, response: str) -> IntentClassification:
        """Parse LLM response into IntentClassification."""
        try:
            # Clean up response
            response = response.strip()
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]

            data = json.loads(response)

            intent_str = data.get("intent", "unknown")
            try:
                intent = IntentType(intent_str)
            except ValueError:
                intent = IntentType.UNKNOWN

            return IntentClassification(
                intent=intent,
                confidence=float(data.get("confidence", 0.7)),
                allowed_tool_groups=INTENT_TOOL_MAP.get(intent, list(TOOL_GROUPS.keys())),
                constraints=data.get("constraints", []),
                skill_hints=data.get("skill_hints", INTENT_SKILL_HINTS.get(intent, [])),
                reasoning=data.get("reasoning"),
            )

        except (json.JSONDecodeError, KeyError) as e:
            trace.trace_router_llm_error(f"Failed to parse LLM response: {e}. Raw: {response[:200]}...")
            return IntentClassification(
                intent=IntentType.UNKNOWN,
                confidence=0.5,
                allowed_tool_groups=list(TOOL_GROUPS.keys()),
                reasoning="LLM response parsing failed",
            )

    def get_allowed_tools(self, classification: IntentClassification) -> list[str]:
        """Get flat list of allowed tool names from classification."""
        tools = []
        for group in classification.allowed_tool_groups:
            tools.extend(TOOL_GROUPS.get(group, []))
        return list(set(tools))  # Deduplicate

    def get_all_tools(self) -> list[str]:
        """Get all available tools across all groups."""
        tools = []
        for group_tools in TOOL_GROUPS.values():
            tools.extend(group_tools)
        return list(set(tools))
