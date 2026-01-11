"""
Tests for ToolRegistry.
"""

import pytest
from tools.registry import ToolRegistry, TOOL_GROUPS, get_registry
from tools.contracts import ToolContract, ToolParameter


class TestToolGroups:
    """Tests for TOOL_GROUPS constant."""

    def test_all_groups_defined(self):
        """Test all expected groups exist."""
        expected = ["memory", "database", "resolution", "web", "home", "skills", "system"]
        for group in expected:
            assert group in TOOL_GROUPS

    def test_memory_group_tools(self):
        """Test memory group tools."""
        assert "search_memories" in TOOL_GROUPS["memory"]
        assert "get_events" in TOOL_GROUPS["memory"]
        assert "get_document" in TOOL_GROUPS["memory"]

    def test_database_group_tools(self):
        """Test database group tools."""
        assert "execute_sql" in TOOL_GROUPS["database"]
        assert "describe_schema" in TOOL_GROUPS["database"]


class TestToolRegistry:
    """Tests for ToolRegistry class."""

    @pytest.fixture
    def empty_registry(self):
        """Create empty registry."""
        return ToolRegistry()

    @pytest.fixture
    def sample_contract(self):
        """Create a sample tool contract."""
        return ToolContract(
            name="test_tool",
            description="A test tool",
            parameters=[
                ToolParameter(
                    name="input",
                    type="string",
                    description="Input value",
                    required=True,
                ),
            ],
        )

    def test_register_tool(self, empty_registry, sample_contract):
        """Test registering a tool."""
        empty_registry.register(sample_contract, groups=["test"])

        assert empty_registry.has_tool("test_tool")

    def test_get_contract(self, empty_registry, sample_contract):
        """Test getting a registered contract."""
        empty_registry.register(sample_contract, groups=["test"])

        contract = empty_registry.get_contract("test_tool")

        assert contract is not None
        assert contract.name == "test_tool"

    def test_get_contract_not_found(self, empty_registry):
        """Test getting non-existent contract."""
        contract = empty_registry.get_contract("nonexistent")

        assert contract is None

    def test_register_with_multiple_groups(self, empty_registry, sample_contract):
        """Test registering tool in multiple groups."""
        empty_registry.register(sample_contract, groups=["group1", "group2"])

        tools_g1 = empty_registry.get_tools_for_groups(["group1"])
        tools_g2 = empty_registry.get_tools_for_groups(["group2"])

        tool_names_g1 = [t.name for t in tools_g1]
        tool_names_g2 = [t.name for t in tools_g2]

        assert "test_tool" in tool_names_g1
        assert "test_tool" in tool_names_g2

    def test_get_tools_for_single_group(self, empty_registry):
        """Test getting tools for a single group."""
        contract1 = ToolContract(
            name="tool1",
            description="Tool 1",
            parameters=[],
        )
        contract2 = ToolContract(
            name="tool2",
            description="Tool 2",
            parameters=[],
        )

        empty_registry.register(contract1, groups=["group_a"])
        empty_registry.register(contract2, groups=["group_b"])

        tools = empty_registry.get_tools_for_groups(["group_a"])
        tool_names = [t.name for t in tools]

        assert "tool1" in tool_names
        assert "tool2" not in tool_names

    def test_get_tools_for_multiple_groups(self, empty_registry):
        """Test getting tools for multiple groups."""
        contract1 = ToolContract(name="tool1", description="Tool 1", parameters=[])
        contract2 = ToolContract(name="tool2", description="Tool 2", parameters=[])
        contract3 = ToolContract(name="tool3", description="Tool 3", parameters=[])

        empty_registry.register(contract1, groups=["group_a"])
        empty_registry.register(contract2, groups=["group_b"])
        empty_registry.register(contract3, groups=["group_c"])

        tools = empty_registry.get_tools_for_groups(["group_a", "group_b"])
        tool_names = [t.name for t in tools]

        assert "tool1" in tool_names
        assert "tool2" in tool_names
        assert "tool3" not in tool_names

    def test_get_all_contracts(self, empty_registry):
        """Test getting all registered contracts."""
        contract1 = ToolContract(name="alpha", description="A", parameters=[])
        contract2 = ToolContract(name="beta", description="B", parameters=[])

        empty_registry.register(contract1, groups=["test"])
        empty_registry.register(contract2, groups=["test"])

        contracts = empty_registry.get_all_contracts()
        names = [c.name for c in contracts]

        assert "alpha" in names
        assert "beta" in names

    def test_get_tool_definitions(self, empty_registry, sample_contract):
        """Test getting tools in OpenAI format."""
        empty_registry.register(sample_contract, groups=["test"])

        openai_tools = empty_registry.get_tool_definitions(["test_tool"])

        assert len(openai_tools) == 1
        assert openai_tools[0]["type"] == "function"
        assert openai_tools[0]["function"]["name"] == "test_tool"

    def test_get_tool_definitions_for_groups(self, empty_registry, sample_contract):
        """Test getting OpenAI tools for groups."""
        empty_registry.register(sample_contract, groups=["test"])

        openai_tools = empty_registry.get_tool_definitions_for_groups(["test"])

        assert len(openai_tools) == 1
        assert openai_tools[0]["function"]["name"] == "test_tool"

    def test_tool_deduplication(self, empty_registry):
        """Test tools in multiple groups aren't duplicated."""
        contract = ToolContract(
            name="shared_tool",
            description="Shared",
            parameters=[],
        )

        empty_registry.register(contract, groups=["group1", "group2"])

        tools = empty_registry.get_tools_for_groups(["group1", "group2"])
        tool_names = [t.name for t in tools]

        # Should only appear once
        assert tool_names.count("shared_tool") == 1

    def test_has_tool(self, empty_registry, sample_contract):
        """Test has_tool method."""
        assert empty_registry.has_tool("test_tool") is False

        empty_registry.register(sample_contract, groups=["test"])

        assert empty_registry.has_tool("test_tool") is True

    def test_list_groups(self, empty_registry):
        """Test list_groups method."""
        groups = empty_registry.list_groups()
        assert isinstance(groups, list)

    def test_list_tools_in_group(self, empty_registry, sample_contract):
        """Test list_tools_in_group method."""
        empty_registry.register(sample_contract, groups=["mygroup"])

        tools = empty_registry.list_tools_in_group("mygroup")
        assert "test_tool" in tools


class TestPreregisteredTools:
    """Tests for pre-registered tools in registry."""

    @pytest.fixture
    def populated_registry(self):
        """Create registry with all standard tools."""
        return get_registry()

    def test_search_memories_registered(self, populated_registry):
        """Test search_memories tool is registered."""
        contract = populated_registry.get_contract("search_memories")
        assert contract is not None
        assert contract.name == "search_memories"

    def test_execute_sql_registered(self, populated_registry):
        """Test execute_sql tool is registered."""
        contract = populated_registry.get_contract("execute_sql")
        assert contract is not None

    def test_resolve_query_registered(self, populated_registry):
        """Test resolve_query tool is registered."""
        contract = populated_registry.get_contract("resolve_query")
        assert contract is not None

    def test_web_search_registered(self, populated_registry):
        """Test web_search tool is registered."""
        contract = populated_registry.get_contract("web_search")
        assert contract is not None

    def test_home_assistant_registered(self, populated_registry):
        """Test home_assistant tool is registered."""
        contract = populated_registry.get_contract("home_assistant")
        assert contract is not None

    def test_all_memory_tools_available(self, populated_registry):
        """Test all memory group tools are available."""
        tool_names = populated_registry.get_tool_names_for_groups(["memory"])

        assert "search_memories" in tool_names
        assert "get_events" in tool_names
        assert "get_document" in tool_names

    def test_all_database_tools_available(self, populated_registry):
        """Test all database group tools are available."""
        tool_names = populated_registry.get_tool_names_for_groups(["database"])

        assert "execute_sql" in tool_names
        assert "describe_schema" in tool_names


class TestRegistryValidation:
    """Tests for validation through registry."""

    @pytest.fixture
    def registry_with_validated_tool(self):
        """Create registry with tool that has validation."""
        registry = ToolRegistry()

        contract = ToolContract(
            name="validated_tool",
            description="Tool with validation",
            parameters=[
                ToolParameter(
                    name="count",
                    type="integer",
                    description="Count",
                    required=True,
                    minimum=1,
                    maximum=100,
                ),
            ],
        )

        registry.register(contract, groups=["test"])
        return registry

    def test_validate_through_registry(self, registry_with_validated_tool):
        """Test validation through registry."""
        contract = registry_with_validated_tool.get_contract("validated_tool")

        # Valid
        is_valid, error, _ = contract.validate_params({"count": 50})
        assert is_valid is True

        # Invalid - out of range
        is_valid, error, _ = contract.validate_params({"count": 200})
        assert is_valid is False
