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

from agent.contact_resolution import FAMILY_RELATIONSHIP_PATTERN, THIRD_PARTY_REFERENCE_PATTERN
from memory_graph_terms import contains_personal_document_term
from observability import trace
from tools.registry import TOOL_GROUPS as REGISTRY_TOOL_GROUPS


class IntentType(str, Enum):
    """Classified intent types."""

    MEMORY_SEARCH = "memory_search"  # Semantic recall over memories, events, documents
    DATA_QUERY = "data_query"  # Structured counting/aggregation over the memory graph
    CONTACT_LOOKUP = "contact_lookup"  # People, relationships
    WEB_SEARCH = "web_search"  # External information
    HOME_CONTROL = "home_control"  # Home Assistant actions
    SKILL_EXECUTION = "skill_execution"  # Running skill scripts
    SYSTEM_COMMAND = "system_command"  # Bash commands
    CONVERSATIONAL = "conversational"  # General chat, no tools needed
    UNKNOWN = "unknown"  # Fallback


class RouteSource(str, Enum):
    """Source of routing decision."""

    UNKNOWN = "unknown"
    FALLBACK = "fallback"
    RULE = "rule"
    LLM = "llm"
    LLM_ERROR = "llm_error"
    LLM_PARSE_ERROR = "llm_parse_error"


@dataclass
class IntentClassification:
    """Result of intent classification."""

    intent: IntentType
    confidence: float
    allowed_tool_groups: list[str]
    constraints: list[str] = field(default_factory=list)
    pre_resolve_contacts: Optional[bool] = None
    reasoning: Optional[str] = None
    route_source: RouteSource = RouteSource.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "allowed_tool_groups": self.allowed_tool_groups,
            "constraints": self.constraints,
            "pre_resolve_contacts": self.pre_resolve_contacts,
            "reasoning": self.reasoning,
            "route_source": self.route_source.value,
        }


# Tool group mappings
# Keep this aligned with the canonical registry to avoid drift.
TOOL_GROUPS = dict(REGISTRY_TOOL_GROUPS)

# Intent to tool group mappings
INTENT_TOOL_MAP = {
    IntentType.MEMORY_SEARCH: ["memory", "resolution"],
    IntentType.DATA_QUERY: ["graph", "memory", "resolution"],
    IntentType.CONTACT_LOOKUP: ["resolution", "memory"],
    IntentType.WEB_SEARCH: ["web"],
    IntentType.HOME_CONTROL: ["home"],
    IntentType.SKILL_EXECUTION: ["skills", "memory"],
    IntentType.SYSTEM_COMMAND: ["system"],
    IntentType.CONVERSATIONAL: ["memory", "resolution", "web", "pdf", "ui"],
    IntentType.UNKNOWN: list(TOOL_GROUPS.keys()),  # All tools
}

# Intent-level fallback policy for contact pre-resolution. The LLM router is
# expected to decide per-query (see prompt below); these defaults only apply
# when the LLM omits the field or rule-based classification short-circuits.
# Conservative defaults: only pre-resolve when the intent itself is about a
# named person.
INTENT_PRE_RESOLVE_CONTACTS = {
    IntentType.MEMORY_SEARCH: False,
    IntentType.DATA_QUERY: False,
    IntentType.CONTACT_LOOKUP: True,
    IntentType.WEB_SEARCH: False,
    IntentType.HOME_CONTROL: False,
    IntentType.SKILL_EXECUTION: False,
    IntentType.SYSTEM_COMMAND: False,
    IntentType.CONVERSATIONAL: False,
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
        llm_request_options: Optional[dict[str, Any]] = None,
    ):
        """
        Initialize the intent router.

        Args:
            llm_base_url: Base URL for LLM API (defaults to INTENT_ROUTER_BASE_URL or LLM_BASE_URL)
            llm_model: Model to use (defaults to INTENT_ROUTER_MODEL or LLM_CHAT_MODEL_FAST)
            llm_api_key: API key (defaults to INTENT_ROUTER_API_KEY or LLM_API_KEY)
            llm_timeout: Timeout for LLM calls (default 20s)
            enable_llm_routing: Whether to use LLM for classification (False = rule-based only)
        """
        self.llm_base_url = llm_base_url or os.getenv(
            "INTENT_ROUTER_BASE_URL", os.getenv("LLM_BASE_URL", "")
        )
        self.llm_model = llm_model or os.getenv(
            "INTENT_ROUTER_MODEL", os.getenv("LLM_CHAT_MODEL_FAST", "")
        )
        self.llm_api_key = llm_api_key or os.getenv(
            "INTENT_ROUTER_API_KEY", os.getenv("LLM_API_KEY", "")
        )
        self.llm_timeout = int(os.getenv("INTENT_ROUTER_TIMEOUT", str(llm_timeout)))
        self.enable_llm_routing = enable_llm_routing
        self.llm_request_options = dict(llm_request_options or {})
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
            route_source=RouteSource.FALLBACK,
        )

    def _rule_based_classify(self, question: str) -> Optional[IntentClassification]:
        """
        Simple rule-based classification using keywords.

        Returns None if confidence is too low for definitive classification.
        """
        q_lower = question.lower()

        # Home control patterns
        # Use word-aware regex matching and action-oriented phrases to avoid
        # substring and generic-term false positives.
        home_patterns = [
            r"\bturn\s+on\b",
            r"\bturn\s+off\b",
            r"\bswitch\s+(on|off)\b",
            r"\blights?\b",
            r"\blamps?\b",
            r"\bthermostat\b",
            r"\b(set|adjust|change)\s+(the\s+)?temperature\b",
            r"\bhome assistant\b",
            r"\bsmart home\b",
            r"\balexa\b",
            r"\bgoogle home\b",
            r"\bhvac\b",
            r"\ba/c\b",
            r"\bair conditioner\b",
            r"\bheater\b",
        ]
        if any(re.search(pattern, q_lower) for pattern in home_patterns):
            return IntentClassification(
                intent=IntentType.HOME_CONTROL,
                confidence=0.9,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.HOME_CONTROL],
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.HOME_CONTROL],
                reasoning="Home control keywords detected",
                route_source=RouteSource.RULE,
            )

        # Web search patterns
        web_keywords = ["search the web", "google", "look up online", "latest news", "news about"]
        if any(kw in q_lower for kw in web_keywords):
            return IntentClassification(
                intent=IntentType.WEB_SEARCH,
                confidence=0.85,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.WEB_SEARCH],
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.WEB_SEARCH],
                reasoning="Web search keywords detected",
                route_source=RouteSource.RULE,
            )

        pdf_keywords = [
            "create a pdf",
            "make a pdf",
            "give me a pdf",
            "generate a pdf",
            "pdf report",
            "downloadable pdf",
            "save as pdf",
        ]
        if any(kw in q_lower for kw in pdf_keywords):
            return IntentClassification(
                intent=IntentType.CONVERSATIONAL,
                confidence=0.9,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.CONVERSATIONAL],
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.CONVERSATIONAL],
                reasoning="PDF generation keywords detected",
                route_source=RouteSource.RULE,
            )

        if self._looks_like_personal_document_memory_query(question):
            return IntentClassification(
                intent=IntentType.MEMORY_SEARCH,
                confidence=0.93,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.MEMORY_SEARCH],
                pre_resolve_contacts=True,
                reasoning=(
                    "Personal document/memory query detected; likely needs memory graph "
                    "document retrieval with contact resolution"
                ),
                route_source=RouteSource.RULE,
            )

        # Contact/people patterns (high precision only)
        contact_patterns = [
            r"\bphone number\b",
            r"\bemail address\b",
            r"\bwho is\b",
            r"\bwho do i know\b",
            r"\brelationship[s]?\b",
            r"\bbirthday\b",
        ]
        if any(re.search(pattern, q_lower) for pattern in contact_patterns):
            return IntentClassification(
                intent=IntentType.CONTACT_LOOKUP,
                confidence=0.85,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.CONTACT_LOOKUP],
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.CONTACT_LOOKUP],
                reasoning="Contact/people keywords detected",
                route_source=RouteSource.RULE,
            )

        # Memory search patterns (high precision only)
        memory_keywords = [
            "search memories",
            "find in memories",
            "look in my memories",
            "find my meetings",
            "meetings from",
            "what happened",
            "last time i",
            "when did i",
        ]
        if any(kw in q_lower for kw in memory_keywords):
            return IntentClassification(
                intent=IntentType.MEMORY_SEARCH,
                confidence=0.8,
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.MEMORY_SEARCH],
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.MEMORY_SEARCH],
                reasoning="Memory search keywords detected",
                route_source=RouteSource.RULE,
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
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.DATA_QUERY],
                reasoning="Data query keywords detected",
                route_source=RouteSource.RULE,
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
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.SYSTEM_COMMAND],
                reasoning="System command keywords detected",
                route_source=RouteSource.RULE,
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
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.CONVERSATIONAL],
                reasoning="Conversational keywords detected",
                route_source=RouteSource.RULE,
            )

        # No clear match - return low confidence result
        return IntentClassification(
            intent=IntentType.UNKNOWN,
            confidence=0.4,
            allowed_tool_groups=list(TOOL_GROUPS.keys()),
            pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.UNKNOWN],
            reasoning="No clear keyword match",
            route_source=RouteSource.RULE,
        )

    def _llm_classify(
        self,
        question: str,
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> IntentClassification:
        """Use LLM to classify the intent."""
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from llm_helpers import call_llm
        from observability.logger import get_runtime_logger

        router_logger = get_runtime_logger(__name__)

        prompt = self._build_classification_prompt(question, conversation_history)
        resolved_model = self.llm_model or None
        router_logger.info(
            "[router._llm_classify] self.llm_model=%r passing model=%r timeout=%s",
            self.llm_model,
            resolved_model,
            self.llm_timeout,
        )

        try:
            content = call_llm(
                prompt,
                timeout=self.llm_timeout,
                model=resolved_model,
                **self.llm_request_options,
            )
            parsed = self._parse_llm_response(content)
            return self._apply_query_heuristics(parsed, question, conversation_history)
        except Exception as e:
            trace.trace_router_llm_error(f"LLM call failed: {e}")
            # Fall back to unknown classification
            return IntentClassification(
                intent=IntentType.UNKNOWN,
                confidence=0.5,
                allowed_tool_groups=list(TOOL_GROUPS.keys()),
                pre_resolve_contacts=INTENT_PRE_RESOLVE_CONTACTS[IntentType.UNKNOWN],
                reasoning=f"LLM call failed: {e}",
                route_source=RouteSource.LLM_ERROR,
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

        return f"""Classify the user's intent based on their question and context.

QUESTION: {question}
{context}
INTENT TYPES:
- memory_search: Qualitative recall over the personal memory graph: events, contacts, documents (personal or related to contacts), places, todos, notes, and the links/connections between them. Pick this when the user wants content that could already exist in their stored graph.
- data_query: Quantitative questions over the memory graph — counts, distinct counts, time-bucketed breakdowns ("how many meetings last month", "how many people did I meet this week", "events grouped by type"). Pick this whenever the answer is a number or ranked breakdown rather than a description.
- contact_lookup: Finding people, relationships, and professions and their information, like phone, email, description, and more.
- web_search: External information from the internet that is NOT expected to live in the user's personal memory graph. Do NOT pick this for personal or contact related documents or information. This is a personal graph, so questions are usually related to it (but not always).
- home_control: Smart home/Home Assistant actions and management
- skill_execution: Running skill scripts
- system_command: Bash/shell commands and system management on a server
- conversational: General chat, content drafting, or generated PDF/document creation requests that do not require a specialized retrieval/control intent.
- unknown: If you are uncertain about the intent, pick this.

Also decide whether pre-resolving contacts is beneficial when creating a answer for the user in upcoming steps:
- Set `pre_resolve_contacts` to true when the query references a specific person by name,
  pronoun, or relationship term (e.g. "my mom", "him", "John") and the answer may depend on that person's events, documents, places, or other graph links.
- Strongly prefer `pre_resolve_contacts=true` for contact-document queries such as "my daughter's prescription", or "my wife's lab results"
- Set `pre_resolve_contacts` to false for:
  - Discovery/ranking queries that ask "who" without naming anyone
    (e.g. "who did I meet most this week?", "who do I talk to the most?",
     "which colleagues have I met recently?", "list everyone I met last month")
  - Aggregation or counting queries over interactions without a named person
    (e.g. "how many people did I meet this week?", "how many meetings did I have?")
  - Web/home/system/conversational requests
  The agent can resolve contacts later during tool execution if needed.

Respond with JSON only:
{{
  "intent": "one of the intent types above",
  "confidence": 0.0 to 1.0,
  "constraints": ["read_only"] or [],
  "pre_resolve_contacts": true or false,
  "reasoning": "brief explanation"
}}"""

    def _parse_llm_response(self, response: str) -> IntentClassification:
        """Parse LLM response into IntentClassification."""
        from llm_helpers import parse_llm_json_content

        try:
            data = parse_llm_json_content(response)

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
                pre_resolve_contacts=pre_resolve_contacts,
                reasoning=data.get("reasoning"),
                route_source=RouteSource.LLM,
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
                route_source=RouteSource.LLM_PARSE_ERROR,
            )

    def _apply_query_heuristics(
        self,
        classification: IntentClassification,
        question: str,
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> IntentClassification:
        """Correct known routing blind spots with deterministic graph-aware heuristics."""
        if self._looks_like_personal_document_memory_query(question, conversation_history):
            return IntentClassification(
                intent=IntentType.MEMORY_SEARCH,
                confidence=max(classification.confidence, 0.94),
                allowed_tool_groups=INTENT_TOOL_MAP[IntentType.MEMORY_SEARCH],
                constraints=classification.constraints,
                pre_resolve_contacts=True,
                reasoning=(
                    "Graph-aware routing override: the request looks like a personal-document "
                    "lookup in the memory graph, so use memory search with contact pre-resolution."
                ),
                route_source=classification.route_source,
            )

        if (
            classification.intent is IntentType.MEMORY_SEARCH
            and classification.pre_resolve_contacts is not True
            and self._has_person_scoped_memory_reference(question, conversation_history)
        ):
            classification.pre_resolve_contacts = True
            if classification.reasoning:
                classification.reasoning = (
                    f"{classification.reasoning} Contact pre-resolution enabled for person-scoped memory lookup."
                )
            else:
                classification.reasoning = "Contact pre-resolution enabled for person-scoped memory lookup."
        return classification

    def _looks_like_personal_document_memory_query(
        self,
        question: str,
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> bool:
        current = (question or "").strip().lower()
        if not current:
            return False

        history_text = " ".join(
            str(message.get("content") or "") for message in (conversation_history or [])[-4:]
        ).lower()
        combined = f"{history_text} {current}".strip()

        has_doc_term = contains_personal_document_term(combined)
        if not has_doc_term:
            return False

        has_family_reference = bool(FAMILY_RELATIONSHIP_PATTERN.search(combined))
        has_third_party_reference = bool(THIRD_PARTY_REFERENCE_PATTERN.search(current))
        has_possessive_reference = bool(re.search(r"\b(my|our)\b", current))
        has_memory_artifact_hint = any(
            phrase in current
            for phrase in (
                "we have a doc",
                "we have a document",
                "in my docs",
                "in my documents",
                "find the doc",
                "find the document",
            )
        )

        return bool(
            has_family_reference
            or (has_third_party_reference and history_text)
            or has_possessive_reference
            or has_memory_artifact_hint
        )

    def _has_person_scoped_memory_reference(
        self,
        question: str,
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> bool:
        current = (question or "").strip().lower()
        history_text = " ".join(
            str(message.get("content") or "") for message in (conversation_history or [])[-4:]
        ).lower()
        combined = f"{history_text} {current}".strip()
        return bool(
            FAMILY_RELATIONSHIP_PATTERN.search(combined)
            or THIRD_PARTY_REFERENCE_PATTERN.search(current)
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
