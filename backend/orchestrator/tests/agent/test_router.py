"""
Tests for IntentRouter.
"""


import pytest

from agent.router import (
    INTENT_TOOL_MAP,
    TOOL_GROUPS,
    IntentClassification,
    IntentRouter,
    IntentType,
)


class TestIntentType:
    """Tests for IntentType enum."""

    def test_all_intent_types_exist(self):
        """Test all expected intent types are defined."""
        expected = [
            "memory_search",
            "data_query",
            "contact_lookup",
            "web_search",
            "home_control",
            "skill_execution",
            "system_command",
            "conversational",
            "complex",
            "unknown",
        ]

        for intent in expected:
            assert IntentType(intent) is not None

    def test_intent_string_value(self):
        """Test IntentType string values."""
        assert IntentType.MEMORY_SEARCH.value == "memory_search"
        assert IntentType.DATA_QUERY.value == "data_query"
        assert IntentType.CONVERSATIONAL.value == "conversational"


class TestIntentClassification:
    """Tests for IntentClassification dataclass."""

    def test_initialization(self):
        """Test IntentClassification creation."""
        classification = IntentClassification(
            intent=IntentType.MEMORY_SEARCH,
            confidence=0.95,
            allowed_tool_groups=["memory", "resolution"],
            constraints=["read_only"],
            skill_hints=["document-search"],
            reasoning="Memory search keywords detected",
        )

        assert classification.intent == IntentType.MEMORY_SEARCH
        assert classification.confidence == 0.95
        assert classification.allowed_tool_groups == ["memory", "resolution"]
        assert classification.constraints == ["read_only"]
        assert classification.skill_hints == ["document-search"]

    def test_to_dict(self):
        """Test IntentClassification serialization."""
        classification = IntentClassification(
            intent=IntentType.DATA_QUERY,
            confidence=0.8,
            allowed_tool_groups=["database"],
        )

        data = classification.to_dict()

        assert data["intent"] == "data_query"
        assert data["confidence"] == 0.8
        assert data["allowed_tool_groups"] == ["database"]


class TestToolGroups:
    """Tests for tool group mappings."""

    def test_all_groups_defined(self):
        """Test all expected tool groups exist."""
        expected_groups = [
            "memory",
            "database",
            "resolution",
            "web",
            "home",
            "skills",
            "system",
        ]

        for group in expected_groups:
            assert group in TOOL_GROUPS

    def test_memory_group_tools(self):
        """Test memory group contains expected tools."""
        assert "search_memories" in TOOL_GROUPS["memory"]
        assert "get_events" in TOOL_GROUPS["memory"]
        assert "get_document" in TOOL_GROUPS["memory"]

    def test_database_group_tools(self):
        """Test database group contains expected tools."""
        assert "execute_sql" in TOOL_GROUPS["database"]
        assert "describe_schema" in TOOL_GROUPS["database"]


class TestIntentToolMap:
    """Tests for intent to tool group mappings."""

    def test_memory_search_tools(self):
        """Test MEMORY_SEARCH intent has correct tool groups."""
        groups = INTENT_TOOL_MAP[IntentType.MEMORY_SEARCH]
        assert "memory" in groups
        assert "resolution" in groups

    def test_conversational_no_tools(self):
        """Test CONVERSATIONAL intent has no tool groups."""
        groups = INTENT_TOOL_MAP[IntentType.CONVERSATIONAL]
        assert groups == []

    def test_complex_all_tools(self):
        """Test COMPLEX intent has all tool groups."""
        groups = INTENT_TOOL_MAP[IntentType.COMPLEX]
        assert set(groups) == set(TOOL_GROUPS.keys())


class TestRuleBasedClassification:
    """Tests for rule-based intent classification."""

    @pytest.fixture
    def router(self):
        """Create router with LLM routing disabled."""
        return IntentRouter(enable_llm_routing=False)

    def test_home_control_keywords(self, router):
        """Test home control intent detection via rule-based."""
        questions = [
            "Turn on the living room lights",
            "Switch off the bedroom lamp",
            "Set thermostat to 72 degrees",
        ]

        for question in questions:
            result = router._rule_based_classify(question)
            assert result is not None
            assert result.intent == IntentType.HOME_CONTROL

    def test_web_search_keywords(self, router):
        """Test web search intent detection via rule-based."""
        questions = [
            "Search the web for Python tutorials",
            "What is the latest news about AI?",
            "Look up online the weather forecast",
        ]

        for question in questions:
            result = router._rule_based_classify(question)
            assert result is not None
            assert result.intent == IntentType.WEB_SEARCH

    def test_memory_search_keywords(self, router):
        """Test memory search intent detection via rule-based."""
        questions = [
            "What meetings did I have last week?",
            "Find documents about project planning",
            "When did I last talk to John?",
        ]

        for question in questions:
            result = router._rule_based_classify(question)
            assert result is not None
            assert result.intent == IntentType.MEMORY_SEARCH

    def test_data_query_keywords(self, router):
        """Test data query intent detection via rule-based."""
        questions = [
            "How many emails did I send?",
            "Query the database for records",
            "Run a SQL statement to get totals",
        ]

        for question in questions:
            result = router._rule_based_classify(question)
            assert result is not None
            assert result.intent == IntentType.DATA_QUERY

    def test_conversational_keywords(self, router):
        """Test conversational intent detection via rule-based."""
        questions = [
            "Hello there",
            "Thanks for your help",
            "Good morning everyone",
        ]

        for question in questions:
            result = router._rule_based_classify(question)
            assert result is not None
            assert result.intent == IntentType.CONVERSATIONAL

    def test_system_command_keywords(self, router):
        """Test system command intent detection via rule-based."""
        questions = [
            "Run command to list files",
            "Execute bash to show users",
            "Use curl to ping the server",
        ]

        for question in questions:
            result = router._rule_based_classify(question)
            assert result is not None
            assert result.intent == IntentType.SYSTEM_COMMAND

    def test_unknown_for_ambiguous(self, router):
        """Test unknown intent for ambiguous questions."""
        result = router._rule_based_classify("xyzabc random string")
        assert result is not None
        assert result.intent == IntentType.UNKNOWN
        assert result.confidence < 0.8


class TestGetAllowedTools:
    """Tests for tool filtering methods."""

    @pytest.fixture
    def router(self):
        return IntentRouter(enable_llm_routing=False)

    def test_get_allowed_tools_single_group(self, router):
        """Test getting tools for single group."""
        classification = IntentClassification(
            intent=IntentType.HOME_CONTROL,
            confidence=0.9,
            allowed_tool_groups=["home"],
        )

        tools = router.get_allowed_tools(classification)

        assert "home_assistant" in tools

    def test_get_allowed_tools_multiple_groups(self, router):
        """Test getting tools for multiple groups."""
        classification = IntentClassification(
            intent=IntentType.MEMORY_SEARCH,
            confidence=0.85,
            allowed_tool_groups=["memory", "resolution"],
        )

        tools = router.get_allowed_tools(classification)

        assert "search_memories" in tools
        assert "get_events" in tools
        assert "resolve_query" in tools
        assert "resolve_contacts" in tools

    def test_get_allowed_tools_empty_groups(self, router):
        """Test getting tools for no groups."""
        classification = IntentClassification(
            intent=IntentType.CONVERSATIONAL,
            confidence=0.9,
            allowed_tool_groups=[],
        )

        tools = router.get_allowed_tools(classification)

        assert tools == []

    def test_get_all_tools(self, router):
        """Test getting all available tools."""
        tools = router.get_all_tools()

        # Should include tools from all groups
        assert "search_memories" in tools
        assert "execute_sql" in tools
        assert "resolve_query" in tools
        assert "web_search" in tools
        assert "home_assistant" in tools


class TestLLMClassification:
    """Tests for LLM-based classification."""

    @pytest.fixture
    def router_with_llm(self):
        """Create router with LLM routing enabled."""
        return IntentRouter(
            llm_base_url="http://localhost:11434/v1",
            llm_model="llama3.3:70b",
            enable_llm_routing=True,
        )

    def test_parse_llm_response_valid(self, router_with_llm):
        """Test parsing valid LLM JSON response."""
        response = '''{
            "intent": "memory_search",
            "confidence": 0.92,
            "constraints": ["read_only"],
            "skill_hints": ["document-search"],
            "reasoning": "User is looking for past meetings"
        }'''

        result = router_with_llm._parse_llm_response(response)

        assert result.intent == IntentType.MEMORY_SEARCH
        assert result.confidence == 0.92
        assert result.constraints == ["read_only"]
        assert result.skill_hints == ["document-search"]

    def test_parse_llm_response_with_markdown(self, router_with_llm):
        """Test parsing LLM response wrapped in markdown."""
        response = '''```json
{
    "intent": "data_query",
    "confidence": 0.85,
    "reasoning": "User wants counts"
}
```'''

        result = router_with_llm._parse_llm_response(response)

        assert result.intent == IntentType.DATA_QUERY

    def test_parse_llm_response_invalid_json(self, router_with_llm):
        """Test parsing invalid JSON returns unknown intent."""
        response = "This is not valid JSON"

        result = router_with_llm._parse_llm_response(response)

        assert result.intent == IntentType.UNKNOWN
        assert result.confidence == 0.5

    def test_parse_llm_response_unknown_intent(self, router_with_llm):
        """Test parsing response with unknown intent type."""
        response = '''{
            "intent": "invalid_intent_type",
            "confidence": 0.9
        }'''

        result = router_with_llm._parse_llm_response(response)

        assert result.intent == IntentType.UNKNOWN


class TestClassificationConfidence:
    """Tests for confidence handling."""

    @pytest.fixture
    def router(self):
        return IntentRouter(enable_llm_routing=False)

    def test_high_confidence_home_control(self, router):
        """Test high confidence for clear home control."""
        result = router._rule_based_classify("Turn on the kitchen lights")
        assert result.confidence >= 0.8

    def test_lower_confidence_ambiguous(self, router):
        """Test lower confidence for ambiguous queries."""
        result = router._rule_based_classify("What about that thing we discussed?")
        assert result.confidence < 0.8
