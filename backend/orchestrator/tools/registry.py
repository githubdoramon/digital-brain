"""
Tool registry with grouping support and optional group filtering helpers.

The registry maintains all tool contracts and provides:
- Tool registration with group assignment
- Group-based lookup/filtering APIs
- OpenAI-format tool definitions
- Singleton access pattern
"""

import logging
import threading
from typing import Any, Optional

from .contracts import (
    ToolContract,
    ToolParameter,
    validate_limit,
)

logger = logging.getLogger(__name__)

# Tool group definitions - maps group names to tool names
TOOL_GROUPS = {
    "memory": ["search_memories", "get_events", "get_document"],
    "resolution": ["resolve_query", "resolve_contacts", "lookup_contact"],
    "web": ["web_search", "fetch_web_page"],
    "home": ["home_assistant"],
    "skills": ["run_skill_script"],
    "system": ["bash"],
}

# Reverse mapping: tool name -> groups
TOOL_TO_GROUPS: dict[str, list[str]] = {}
for group, tools in TOOL_GROUPS.items():
    for tool in tools:
        if tool not in TOOL_TO_GROUPS:
            TOOL_TO_GROUPS[tool] = []
        TOOL_TO_GROUPS[tool].append(group)


class ToolRegistry:
    """
    Registry of all tool contracts with grouping support.

    Provides:
    - Tool registration and lookup
    - Filtering by group (optional helper API)
    - OpenAI-format tool definitions
    """

    def __init__(self):
        self._contracts: dict[str, ToolContract] = {}
        self._groups: dict[str, list[str]] = dict(TOOL_GROUPS)
        self._lock = threading.Lock()

    def register(
        self,
        contract: ToolContract,
        groups: Optional[list[str]] = None,
    ) -> None:
        """
        Register a tool contract.

        Args:
            contract: The tool contract to register
            groups: Optional list of groups (uses TOOL_TO_GROUPS if not specified)
        """
        with self._lock:
            self._contracts[contract.name] = contract

            # Add to groups
            tool_groups = groups or TOOL_TO_GROUPS.get(contract.name, [])
            for group in tool_groups:
                if group not in self._groups:
                    self._groups[group] = []
                if contract.name not in self._groups[group]:
                    self._groups[group].append(contract.name)

    def get_contract(self, name: str) -> Optional[ToolContract]:
        """Get a tool contract by name."""
        return self._contracts.get(name)

    def get_all_contracts(self) -> list[ToolContract]:
        """Get all registered tool contracts."""
        return list(self._contracts.values())

    def get_tools_for_groups(self, groups: list[str]) -> list[ToolContract]:
        """
        Get all tool contracts for the specified groups.

        Used by router/metadata workflows and tests; optional at runtime.
        """
        tool_names = set()
        for group in groups:
            tool_names.update(self._groups.get(group, []))

        return [self._contracts[name] for name in tool_names if name in self._contracts]

    def get_tool_names_for_groups(self, groups: list[str]) -> list[str]:
        """Get tool names for the specified groups."""
        tool_names = set()
        for group in groups:
            tool_names.update(self._groups.get(group, []))
        return list(tool_names)

    def get_tool_definitions(
        self, allowed_tools: Optional[list[str]] = None
    ) -> list[dict[str, Any]]:
        """
        Get OpenAI-format tool definitions.

        Args:
            allowed_tools: If specified, only include these tools

        Returns:
            List of tool definitions in OpenAI format
        """
        definitions = []
        for name, contract in self._contracts.items():
            if allowed_tools is None or name in allowed_tools:
                definitions.append(contract.to_openai_tool())
        return definitions

    def get_tool_definitions_for_groups(self, groups: list[str]) -> list[dict[str, Any]]:
        """Get OpenAI-format tool definitions for specific groups."""
        allowed = self.get_tool_names_for_groups(groups)
        return self.get_tool_definitions(allowed)

    def list_groups(self) -> list[str]:
        """List all available tool groups."""
        return list(self._groups.keys())

    def list_tools_in_group(self, group: str) -> list[str]:
        """List all tools in a specific group."""
        return self._groups.get(group, [])

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._contracts


# Singleton instance
_registry: Optional[ToolRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> ToolRegistry:
    """
    Get the singleton tool registry.

    The registry is initialized with all tool contracts on first access.
    """
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ToolRegistry()
                _register_all_tools(_registry)
    return _registry


def _register_all_tools(registry: ToolRegistry) -> None:
    """Register all tool contracts with the registry."""

    # search_memories
    registry.register(
        ToolContract(
            name="search_memories",
            description=(
                "Perform semantic (vector) search over the user's memories, "
                "including events, moments, documents, and other recorded information. "
                "Use this for exploratory queries when you need to find relevant "
                "information based on meaning rather than exact matches."
            ),
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="A natural-language search phrase describing what to find.",
                    required=True,
                    min_length=1,
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Maximum results to return (default 30).",
                    required=False,
                    default=30,
                    minimum=1,
                    maximum=50,
                    validator=validate_limit,
                ),
                ToolParameter(
                    name="time_start",
                    type="string",
                    description="ISO 8601 timestamp for the earliest results (optional).",
                    required=False,
                ),
                ToolParameter(
                    name="time_end",
                    type="string",
                    description="ISO 8601 timestamp for the latest results (optional).",
                    required=False,
                ),
                ToolParameter(
                    name="contact_ids",
                    type="array",
                    description="Filter by contact IDs (optional).",
                    required=False,
                    items_type="string",
                ),
                ToolParameter(
                    name="sort_order",
                    type="string",
                    description=(
                        "Optional ordering mode: 'relevance' (default), "
                        "'newest' (most recent first), or 'oldest' (earliest first)."
                    ),
                    required=False,
                    default="relevance",
                    enum=["relevance", "newest", "oldest"],
                ),
                ToolParameter(
                    name="tags",
                    type="array",
                    description="Filter by tags - can be major tags (e.g., 'Health') or minor tags (e.g., 'prescription'). Multiple tags use OR logic.",
                    required=False,
                    items_type="string",
                ),
            ],
            constraints=["read_only"],
        )
    )

    # resolve_query
    registry.register(
        ToolContract(
            name="resolve_query",
            description=(
                "Extract structured entities from a natural-language query: contacts (people), "
                "places, and time ranges. Returns IDs you can use in other tool calls."
            ),
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="The user's natural-language query to parse.",
                    required=True,
                    min_length=1,
                ),
            ],
            constraints=["read_only"],
        )
    )

    # resolve_contacts
    registry.register(
        ToolContract(
            name="resolve_contacts",
            description=(
                "Extract people from free-form text and resolve them to existing contacts. "
                "Handles relationships (e.g., 'my daughter') and nested relationships "
                "('my daughter's doctor'), returns candidates when ambiguous, and "
                "suggests missing relationships plus inferred professions for new contacts. "
                "Provide only natural-language text; runtime user context is injected by the controller."
            ),
            parameters=[
                ToolParameter(
                    name="text",
                    type="string",
                    description="The text to analyze for person mentions.",
                    required=True,
                    min_length=1,
                ),
            ],
            constraints=["read_only"],
        )
    )

    # get_events
    registry.register(
        ToolContract(
            name="get_events",
            description=(
                "Retrieve full details for specific events by their IDs. "
                "Use after search_memories to get complete event information "
                "including attendees, location, and full content."
            ),
            parameters=[
                ToolParameter(
                    name="event_ids",
                    type="array",
                    description="List of event IDs to retrieve.",
                    required=True,
                    items_type="string",
                ),
            ],
            constraints=["read_only"],
        )
    )

    # get_document
    registry.register(
        ToolContract(
            name="get_document",
            description=(
                "Retrieve full content of a document by its ID. "
                "Use after search_memories to get the complete document text and metadata."
            ),
            parameters=[
                ToolParameter(
                    name="document_id",
                    type="string",
                    description="The document ID to retrieve.",
                    required=True,
                ),
            ],
            constraints=["read_only"],
        )
    )

    # web_search
    registry.register(
        ToolContract(
            name="web_search",
            description=(
                "Search the web for current information. Use when the user asks about "
                "external topics, news, or information not in their personal database."
            ),
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="The search query.",
                    required=True,
                    min_length=1,
                ),
                ToolParameter(
                    name="max_results",
                    type="integer",
                    description="Maximum results to return (default 5).",
                    required=False,
                    default=5,
                    minimum=1,
                    maximum=10,
                ),
            ],
            constraints=["read_only"],
        )
    )

    # fetch_web_page
    registry.register(
        ToolContract(
            name="fetch_web_page",
            description=(
                "Fetch a web page and extract its main content using the configured web "
                "extraction service."
            ),
            parameters=[
                ToolParameter(
                    name="url",
                    type="string",
                    description="The URL to fetch (http/https).",
                    required=True,
                    min_length=1,
                ),
                ToolParameter(
                    name="include_links",
                    type="boolean",
                    description="Whether to include extracted links (default false).",
                    required=False,
                ),
                ToolParameter(
                    name="include_images",
                    type="boolean",
                    description="Whether to include extracted image URLs (default false).",
                    required=False,
                ),
                ToolParameter(
                    name="include_raw_html",
                    type="boolean",
                    description="Whether to include raw HTML in the response (default false).",
                    required=False,
                ),
                ToolParameter(
                    name="max_characters",
                    type="integer",
                    description="Maximum characters to return (default 20000).",
                    required=False,
                    minimum=100,
                    maximum=20000,
                ),
            ],
        )
    )

    # run_skill_script
    registry.register(
        ToolContract(
            name="run_skill_script",
            description=(
                "Execute a script from an active skill. Only available when a skill with scripts "
                "is active. Check activated skills for available scripts."
            ),
            parameters=[
                ToolParameter(
                    name="skill_name",
                    type="string",
                    description="Name of the active skill containing the script.",
                    required=True,
                ),
                ToolParameter(
                    name="script_name",
                    type="string",
                    description="Name of the script file to execute (e.g., 'generate.py').",
                    required=True,
                ),
                ToolParameter(
                    name="args",
                    type="object",
                    description="Arguments to pass to the script as JSON.",
                    required=False,
                ),
            ],
        )
    )

    # bash
    registry.register(
        ToolContract(
            name="bash",
            description=(
                "Execute a shell command and return stdout, stderr, and exit code. "
                "Use this for CLI operations like curl, jq, file manipulation, or any system command. "
                "Commands run in a sandboxed environment with timeout protection."
            ),
            parameters=[
                ToolParameter(
                    name="command",
                    type="string",
                    description="The shell command to execute (e.g., 'curl -s https://api.example.com/data | jq .name').",
                    required=True,
                    min_length=1,
                ),
                ToolParameter(
                    name="timeout",
                    type="integer",
                    description="Maximum execution time in seconds (default 30, max 120).",
                    required=False,
                    default=30,
                    minimum=1,
                    maximum=120,
                ),
            ],
        )
    )

    # home_assistant
    registry.register(
        ToolContract(
            name="home_assistant",
            description=(
                "Control Home Assistant smart home/office devices via MCP protocol. "
                "TWO-STEP PROCESS REQUIRED:\n"
                "1. FIRST call with action='list_tools' to discover available HA tools\n"
                "2. THEN use action='call_tool' with the correct tool from the list\n\n"
                "TOOL SELECTION GUIDE (after listing):\n"
                "- To TURN OFF: Use 'HassTurnOff' with arguments={'name': 'device name'}\n"
                "- To TURN ON: Use 'HassTurnOn' with arguments={'name': 'device name'}\n"
                "- To SET BRIGHTNESS: Use 'HassLightSet' with name and brightness\n"
                "NEVER guess tool names. NEVER use entity_id - use friendly 'name' instead."
            ),
            parameters=[
                ToolParameter(
                    name="action",
                    type="string",
                    description="Action to perform: 'list_tools' MUST be called first to discover available tools. Only use 'call_tool' after you have the tool list.",
                    required=True,
                    enum=["list_tools", "call_tool"],
                ),
                ToolParameter(
                    name="tool_name",
                    type="string",
                    description="Name of the MCP tool to call (e.g., 'HassTurnOff', 'HassTurnOn', 'HassLightSet'). MUST be from the list_tools response.",
                    required=False,
                ),
                ToolParameter(
                    name="arguments",
                    type="object",
                    description="Arguments for the MCP tool. Most tools use 'name' (friendly device name like 'office lights') - NOT entity_id.",
                    required=False,
                ),
            ],
        )
    )

    # lookup_contact - Smart contact search and relationship lookup
    registry.register(
        ToolContract(
            name="lookup_contact",
            description=(
                "Smart contact lookup with fuzzy matching. "
                "Use this instead of raw SQL queries for contact-related questions.\n\n"
                "ACTIONS:\n"
                "- 'search': Find contacts by name, email, or phone (handles typos, partial names, nicknames, aliases)\n"
                "- 'get_relationships': Get a contact's relationships with full details\n"
                "- 'find_related': Find a contact AND their related contacts in one call\n\n"
                "EXAMPLES:\n"
                "- 'Who is John Smith?' → action='search', query='John Smith'\n"
                "- 'List Maria's family' → action='find_related', query='Maria' (then filter results by family-type relationships)\n"
                "- 'Who reports to David?' → action='find_related', query='David', relationship_types=['report', 'direct-report']"
            ),
            parameters=[
                ToolParameter(
                    name="action",
                    type="string",
                    description="Action: 'search' to find contacts, 'get_relationships' for a contact's connections, 'find_related' to find contact + their relationships.",
                    required=True,
                    enum=["search", "get_relationships", "find_related"],
                ),
                ToolParameter(
                    name="query",
                    type="string",
                    description="Search query - can be a name (partial, full, nickname, alias), email, or phone number.",
                    required=False,
                ),
                ToolParameter(
                    name="contact_id",
                    type="string",
                    description="Contact ID for get_relationships action. If not provided, query will be used to find the contact.",
                    required=False,
                ),
                ToolParameter(
                    name="search_by",
                    type="string",
                    description="Search mode for 'search' action: 'name', 'email', 'phone', or 'any' (default).",
                    required=False,
                    enum=["name", "email", "phone", "any"],
                ),
                ToolParameter(
                    name="relationship_types",
                    type="array",
                    description="Filter by specific relationship types. Optional - if not provided, all relationships are returned and you can filter based on context.",
                    required=False,
                    items_type="string",
                ),
                ToolParameter(
                    name="fuzzy_threshold",
                    type="integer",
                    description="Minimum fuzzy match score (0-100). Lower = more lenient. Default 75.",
                    required=False,
                    default=75,
                    minimum=0,
                    maximum=100,
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Maximum contacts to return for search action. Default 10.",
                    required=False,
                    default=10,
                    minimum=1,
                    maximum=50,
                    validator=validate_limit,
                ),
            ],
            constraints=["read_only"],
        )
    )

    logger.info("[tools.registry] Registered %s tools", len(registry._contracts))
