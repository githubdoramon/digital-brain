"""
LLM-based intent router for task classification.

The intent router:
1. Classifies the user's intent
2. Provides skill hints for the skill matcher

This is a separate, dedicated LLM call at the start of each request.
It can use a smaller/faster model for efficiency.
"""

import json
import os
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from time import perf_counter
from typing import Any, Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from observability import trace
from tools.registry import TOOL_GROUPS as REGISTRY_TOOL_GROUPS


class IntentType(str, Enum):
    """Classified intent types."""

    MEMORY_SEARCH = "memory_search"  # Searching memories, events, documents
    DATA_QUERY = "data_query"  # Structured retrieval/counting (non-SQL)
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
    pre_resolve_contacts: Optional[bool] = None
    reasoning: Optional[str] = None
    route_source: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "allowed_tool_groups": self.allowed_tool_groups,
            "constraints": self.constraints,
            "skill_hints": self.skill_hints,
            "pre_resolve_contacts": self.pre_resolve_contacts,
            "reasoning": self.reasoning,
            "route_source": self.route_source,
        }


# Tool group mappings
# Keep this aligned with the canonical registry to avoid drift.
TOOL_GROUPS = dict(REGISTRY_TOOL_GROUPS)

# Intent to tool group mappings
INTENT_TOOL_MAP = {
    IntentType.MEMORY_SEARCH: ["memory", "resolution"],
    IntentType.DATA_QUERY: ["memory", "resolution"],
    IntentType.CONTACT_LOOKUP: ["resolution", "memory"],
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

# Intent-level fallback policy for contact pre-resolution.
INTENT_PRE_RESOLVE_CONTACTS = {
    IntentType.MEMORY_SEARCH: True,
    IntentType.DATA_QUERY: True,
    IntentType.CONTACT_LOOKUP: True,
    IntentType.WEB_SEARCH: False,
    IntentType.HOME_CONTROL: False,
    IntentType.SKILL_EXECUTION: False,
    IntentType.SYSTEM_COMMAND: False,
    IntentType.CONVERSATIONAL: False,
    IntentType.COMPLEX: True,
    IntentType.UNKNOWN: False,
}


class IntentRouter:
    """
    LLM-based intent router for task classification.

    Produces intent classification and hints via a separate LLM call.
    Tool visibility is controlled by the controller.
    """

    def __init__(
        self,
        llm_base_url: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        llm_timeout: int = 20,
        enable_llm_routing: bool = True,
    ):
        """
        Initialize the intent router.

        Args:
            llm_base_url: Base URL for LLM API (defaults to INTENT_ROUTER_BASE_URL or LLM_BASE_URL)
            llm_model: Model to use (defaults to INTENT_ROUTER_MODEL or LLM_CHAT_MODEL)
            llm_api_key: API key (defaults to INTENT_ROUTER_API_KEY or LLM_API_KEY)
            llm_timeout: Timeout for LLM calls (default 20s)
            enable_llm_routing: Whether to use LLM for classification (False = rule-based only)
        """
        self.llm_base_url = llm_base_url or os.getenv(
            "INTENT_ROUTER_BASE_URL", os.getenv("LLM_BASE_URL", "")
        )
        self.llm_model = llm_model or os.getenv(
            "INTENT_ROUTER_MODEL", os.getenv("LLM_CHAT_MODEL_SIMPLER", "")
        )
        self.llm_api_key = llm_api_key or os.getenv(
            "INTENT_ROUTER_API_KEY", os.getenv("LLM_API_KEY", "")
        )
        self.llm_timeout = int(os.getenv("INTENT_ROUTER_TIMEOUT", str(llm_timeout)))
        self.enable_llm_routing = enable_llm_routing
        self.rule_high_confidence_threshold = float(
            os.getenv("INTENT_ROUTER_RULE_HIGH_CONFIDENCE", "0.85")
        )

    def confidence_tier(self, confidence: float) -> str:
        """Map confidence into coarse routing tiers."""
        high = float(os.getenv("ROUTER_HIGH_CONFIDENCE_THRESHOLD", "0.80"))
        medium = float(os.getenv("ROUTER_MEDIUM_CONFIDENCE_THRESHOLD", "0.60"))
        if confidence >= high:
            return "high"
        if confidence >= medium:
            return "medium"
        return "low"

    async def classify(
        self,
        question: str,
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> IntentClassification:
        """
        Classify the user's intent.

        Uses LLM-first classification, then rule-based fallback if LLM is
        disabled/unavailable or returns an error.

        Args:
            question: The user's question
            conversation_history: Optional conversation history for context

        Returns:
            IntentClassification with intent, tool groups, and constraints
        """
        start_time = trace.trace_router_start(question)

        # Conservative hybrid routing:
        # 1) high-precision deterministic match can short-circuit
        # 2) otherwise LLM handles open-ended language
        rule_result = self._rule_based_classify(question)
        if rule_result and rule_result.confidence >= self.rule_high_confidence_threshold:
            duration_ms = (perf_counter() - start_time) * 1000
            trace.trace_router_rule_match(
                rule_result.intent.value,
                rule_result.confidence,
                rule_result.reasoning or "",
                rule_result.allowed_tool_groups,
                duration_ms,
            )
            return rule_result

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
            duration_ms = (perf_counter() - start_time) * 1000
            trace.trace_router_rule_match(
                rule_result.intent.value,
                rule_result.confidence,
                rule_result.reasoning or "",
                rule_result.allowed_tool_groups,
                duration_ms,
            )
            trace.trace_router_fallback(rule_result.intent.value, "Using rule-based result")
            return rule_result

        trace.trace_router_fallback(IntentType.UNKNOWN.value, "No classification match")
        return IntentClassification(
            intent=IntentType.UNKNOWN,
            confidence=0.5,
            allowed_tool_groups=list(TOOL_GROUPS.keys()),
            pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.UNKNOWN],
            reasoning="Fallback to all tools",
            route_source="fallback",
        )

    def _rule_based_classify(self, question: str) -> Optional[IntentClassification]:
        """
        Simple rule-based classification using keywords.

        Returns None if confidence is too low for definitive classification.
        """
        q_lower = question.lower()

        # Home control patterns
        home_keywords = [
            "turn on",
            "turn off",
            "switch",
            "light",
            "lamp",
            "thermostat",
            "temperature",
            "home assistant",
            "smart home",
            "alexa",
            "google home",
            "hvac",
            "ac",
            "heater",
            "office",
            "heater",
        ]
        if any(kw in q_lower for kw in home_keywords):
            return IntentClassification(
                intent=IntentType.HOME_CONTROL,
                confidence=0.9,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.HOME_CONTROL],
                skill_hints=INTENT_SKILL_HINTS[IntentType.HOME_CONTROL],
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.HOME_CONTROL],
                reasoning="Home control keywords detected",
                route_source="rule",
            )

        # Web search patterns
        web_keywords = ["search the web", "google", "look up online", "latest news", "news about"]
        if any(kw in q_lower for kw in web_keywords):
            return IntentClassification(
                intent=IntentType.WEB_SEARCH,
                confidence=0.85,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.WEB_SEARCH],
                skill_hints=INTENT_SKILL_HINTS[IntentType.WEB_SEARCH],
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.WEB_SEARCH],
                reasoning="Web search keywords detected",
                route_source="rule",
            )

        # Contact/people patterns (high precision only)
        contact_patterns = [
            r"\bphone number\b",
            r"\bemail address\b",
            r"\bwho is\b",
            r"\bwho do I know\b",
            r"\brelationship[s]?\b",
            r"\bbirthday\b",
        ]
        if any(re.search(pattern, q_lower) for pattern in contact_patterns):
            return IntentClassification(
                intent=IntentType.CONTACT_LOOKUP,
                confidence=0.85,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.CONTACT_LOOKUP],
                skill_hints=INTENT_SKILL_HINTS[IntentType.CONTACT_LOOKUP],
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.CONTACT_LOOKUP],
                reasoning="Contact/people keywords detected",
                route_source="rule",
            )

        # Memory search patterns (high precision only)
        memory_keywords = [
            "search memories",
            "find in memories",
            "look in my memories",
            "what happened",
            "last time i",
            "when did i",
        ]
        if any(kw in q_lower for kw in memory_keywords):
            return IntentClassification(
                intent=IntentType.MEMORY_SEARCH,
                confidence=0.8,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.MEMORY_SEARCH],
                skill_hints=INTENT_SKILL_HINTS[IntentType.MEMORY_SEARCH],
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.MEMORY_SEARCH],
                reasoning="Memory search keywords detected",
                route_source="rule",
            )

        # Data query/count patterns
        sql_keywords = [
            "how many",
            "count",
            "list all",
            "show all",
            "aggregate",
            "sum",
            "average",
            "total",
        ]
        if any(kw in q_lower for kw in sql_keywords):
            return IntentClassification(
                intent=IntentType.DATA_QUERY,
                confidence=0.8,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.DATA_QUERY],
                skill_hints=INTENT_SKILL_HINTS[IntentType.DATA_QUERY],
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.DATA_QUERY],
                reasoning="Data query keywords detected",
                route_source="rule",
            )

        # System command patterns
        system_keywords = [
            "run command",
            "execute",
            "bash",
            "shell",
            "curl",
            "script",
            "terminal",
        ]
        if any(kw in q_lower for kw in system_keywords):
            return IntentClassification(
                intent=IntentType.SYSTEM_COMMAND,
                confidence=0.85,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.SYSTEM_COMMAND],
                skill_hints=INTENT_SKILL_HINTS[IntentType.SYSTEM_COMMAND],
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.SYSTEM_COMMAND],
                reasoning="System command keywords detected",
                route_source="rule",
            )

        # Conversational patterns (greetings, thanks, etc.)
        conversational_keywords = [
            "hello",
            "hi ",
            "hey ",
            "thanks",
            "thank you",
            "goodbye",
            "bye",
            "how are you",
            "good morning",
            "good night",
            "help",
            "what can you do",
        ]
        if any(kw in q_lower for kw in conversational_keywords):
            return IntentClassification(
                intent=IntentType.CONVERSATIONAL,
                confidence=0.9,
                allowed_tool_groups=[],
                skill_hints=[],
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.CONVERSATIONAL],
                reasoning="Conversational keywords detected",
                route_source="rule",
            )

        # No clear match - return low confidence result
        return IntentClassification(
            intent=IntentType.UNKNOWN,
            confidence=0.4,
            allowed_tool_groups=list(TOOL_GROUPS.keys()),
            pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.UNKNOWN],
            reasoning="No clear keyword match",
            route_source="rule",
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
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.UNKNOWN],
                reasoning=f"LLM call failed: {e}",
                route_source="llm_error",
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
            context = "\n".join(f"{msg['role']}: {msg['content'][:200]}" for msg in recent)
            context = f"\nRECENT CONTEXT:\n{context}\n"

        return f"""Classify the user's intent to determine which tools are needed.

QUESTION: {question}
{context}
INTENT TYPES:
- memory_search: Searching memories, events, documents
- data_query: Counting, aggregation, structured retrieval (no SQL tool)
- contact_lookup: Finding people, relationships
- web_search: External information from the internet
- home_control: Smart home/Home Assistant actions
- skill_execution: Running skill scripts
- system_command: Bash/shell commands
- conversational: General chat, no tools needed
- complex: Multi-step task needing multiple tool groups

Also decide whether the controller should pre-resolve contacts before the tool loop:
- Set `pre_resolve_contacts` to true when early contact identification or disambiguation is likely useful
  (for example person-referential memory/data/contact queries).
- Set it to false for web/home/system/conversational requests.

Respond with JSON only:
{{
  "intent": "one of the intent types above",
  "confidence": 0.0 to 1.0,
  "constraints": ["read_only"] or [],
  "skill_hints": ["relevant-skill-names"] or [],
  "pre_resolve_contacts": true or false,
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

            raw_pre_resolve = data.get(
                "pre_resolve_contacts",
                INTENT_PRE_RESOLVE_CONTACTS.get(intent, False),
            )
            if isinstance(raw_pre_resolve, bool):
                pre_resolve_contacts = raw_pre_resolve
            elif isinstance(raw_pre_resolve, str):
                pre_resolve_contacts = raw_pre_resolve.strip().lower() in {"1", "true", "yes"}
            else:
                pre_resolve_contacts = bool(raw_pre_resolve)

            return IntentClassification(
                intent=intent,
                confidence=float(data.get("confidence", 0.7)),
                allowed_tool_groups=INTENT_TOOL_MAP.get(intent, list(TOOL_GROUPS.keys())),
                constraints=data.get("constraints", []),
                skill_hints=data.get("skill_hints", INTENT_SKILL_HINTS.get(intent, [])),
                pre_resolve_contacts=pre_resolve_contacts,
                reasoning=data.get("reasoning"),
                route_source="llm",
            )

        except (json.JSONDecodeError, KeyError) as e:
            trace.trace_router_llm_error(
                f"Failed to parse LLM response: {e}. Raw: {response[:200]}..."
            )
            return IntentClassification(
                intent=IntentType.UNKNOWN,
                confidence=0.5,
                allowed_tool_groups=list(TOOL_GROUPS.keys()),
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.UNKNOWN],
                reasoning="LLM response parsing failed",
                route_source="llm_parse_error",
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
