"""
Tool registry with grouping support and optional group filtering helpers.

The registry maintains all tool contracts and provides:
- Tool registration with group assignment
- Group-based lookup/filtering APIs
- OpenAI-format tool definitions
- Singleton access pattern
"""

import threading
from typing import Any, Optional

from observability.logger import get_runtime_logger
from ui_dsl.validator import validate_ui_directive_tool_param

from .contracts import (
    ToolContract,
    ToolParameter,
    validate_positive_int,
)

logger = get_runtime_logger(__name__)

# Tool group definitions - maps group names to tool names
TOOL_GROUPS = {
    "memory": ["search_memories", "get_events", "get_document"],
    "resolution": [
        "resolve_contacts",
        "lookup_contact",
        "select_contacts",
        "lookup_places",
        "lookup_contact_places",
    ],
    "web": ["web_search", "fetch_web_page"],
    "home": ["home_assistant"],
    "skills": ["run_skill_script"],
    "ui": ["emit_ui_directive"],
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
                "Semantic retrieval tool across personal events, notes, and documents. "
                "Best for topic-based or content-based discovery (e.g. 'what did we discuss about project X'). "
                "Can find both formal calendar meetings and informal interactions captured in personal event summaries/snippets. "
                "Do NOT use this for time-window enumeration, counting, or ranking queries (e.g. 'who did I meet most this week') — "
                "use `get_events(action=by_time_span)` instead, which returns all events in a window without semantic filtering. "
                "Use `contact_ids` only when the user named specific people and identity is resolved."
            ),
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description=(
                        "Natural-language retrieval topic. Prefer semantic intent terms (for example interactions, calls, 1:1, lunch, "
                        "conversation) over narrow single labels. Avoid stuffing person names when `contact_ids` are provided."
                    ),
                    required=True,
                    min_length=1,
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description=(
                        "Maximum rows to return. Use smaller values for focused follow-ups and larger "
                        "values for broad discovery."
                    ),
                    required=False,
                    default=30,
                    minimum=1,
                    validator=validate_positive_int,
                ),
                ToolParameter(
                    name="time_start",
                    type="string",
                    description=(
                        "Lower time bound in ISO 8601. Use for future/upcoming windows or bounded ranges."
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="time_end",
                    type="string",
                    description=(
                        "Upper time bound in ISO 8601. Use for historical 'latest/last' questions to avoid future leakage."
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="contact_ids",
                    type="array",
                    description=(
                        "Optional contact scope filter by resolved contact IDs. Use when the user explicitly asks about named people. "
                        "Do not add broad/self filters for 'who did I meet/talk to most' style discovery queries."
                    ),
                    required=False,
                    items_type="string",
                ),
                ToolParameter(
                    name="sort_order",
                    type="string",
                    description=(
                        "Ordering mode. Use `newest` for most-recent questions, `oldest` for first/earliest, "
                        "and `relevance` for topical discovery."
                    ),
                    required=False,
                    default="relevance",
                    enum=["relevance", "newest", "oldest"],
                ),
                ToolParameter(
                    name="tags",
                    type="array",
                    description=(
                        "Optional tag filter for narrowing by domain. Multiple tags use OR logic."
                    ),
                    required=False,
                    items_type="string",
                ),
            ],
        )
    )

    # resolve_contacts
    registry.register(
        ToolContract(
            name="resolve_contacts",
            description=(
                "Person-resolution tool. Use when a question references people and you need reliable contact IDs. "
                "Handles aliases, relationships, and ambiguity. If ambiguous, this returns clarification payloads "
                "instead of guessing. Pass only `text`; runtime identity context is injected by the controller. "
                "Best for explicit person mentions in the user request, not for guessing participants from event titles."
            ),
            parameters=[
                ToolParameter(
                    name="text",
                    type="string",
                    description="User text containing person references to resolve into contact identities.",
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
                "Event-detail retrieval tool with two modes: `by_ids` (fetch specific event records) and "
                "`by_time_span` (strict chronological retrieval). This is the preferred tool when the question is "
                "about who attended events or how often interactions happened in a time window. Use `by_time_span` "
                "for strict event listing/ranking workflows, and `by_ids` after `search_memories` candidate discovery. "
                "Do not use this for document content questions (use `get_document`) or pure contact profile lookup (use `lookup_contact`)."
            ),
            parameters=[
                ToolParameter(
                    name="action",
                    type="string",
                    description=(
                        "Retrieval mode: `by_ids` for explicit event IDs, or `by_time_span` for strict time-window listing. "
                        "Examples: use `by_ids` after `search_memories`; use `by_time_span` for queries like 'who did I meet most this week'."
                    ),
                    required=False,
                    enum=["by_ids", "by_time_span"],
                ),
                ToolParameter(
                    name="event_ids",
                    type="array",
                    description=(
                        "Event IDs for action=`by_ids`. Usually sourced from `search_memories` results. "
                        "Not used with action=`by_time_span`."
                    ),
                    required=False,
                    items_type="string",
                ),
                ToolParameter(
                    name="time_start",
                    type="string",
                    description=(
                        "ISO 8601 lower bound for action=`by_time_span` (inclusive). "
                        "Use for strict windows like 'this week' or 'last month'."
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="time_end",
                    type="string",
                    description="ISO 8601 upper bound for action=`by_time_span` (inclusive).",
                    required=False,
                ),
                ToolParameter(
                    name="contact_ids",
                    type="array",
                    description=(
                        "Optional contact-ID filter for action=`by_time_span`. Use when contacts are already resolved and you need "
                        "events involving specific people."
                    ),
                    required=False,
                    items_type="string",
                ),
                ToolParameter(
                    name="sort_order",
                    type="string",
                    description="Sort order for action=`by_time_span`: `newest` or `oldest`.",
                    required=False,
                    default="newest",
                    enum=["newest", "oldest"],
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Maximum events to return for action=`by_time_span`.",
                    required=False,
                    default=50,
                    minimum=1,
                    validator=validate_positive_int,
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
                "Detail retrieval for document candidates surfaced by `search_memories`. Use when answer quality "
                "depends on exact document fields/content."
            ),
            parameters=[
                ToolParameter(
                    name="document_id",
                    type="string",
                    description="Document ID from search results.",
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
                "External web search for non-personal knowledge. Use only when the answer is not expected "
                "to exist in personal memories."
            ),
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Web query string for external information.",
                    required=True,
                    min_length=1,
                ),
                ToolParameter(
                    name="max_results",
                    type="integer",
                    description="Result cap for web snippets. Keep small unless user asks for breadth.",
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
                "Fetch and extract content from a specific URL. Use after `web_search` when you need details from one source."
            ),
            parameters=[
                ToolParameter(
                    name="url",
                    type="string",
                    description="Absolute http/https URL to fetch.",
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
                "Run shell commands for explicit system/CLI tasks. Do not use for normal memory retrieval or contact lookup. "
                "Keep commands minimal, deterministic, and bounded by timeout."
            ),
            parameters=[
                ToolParameter(
                    name="command",
                    type="string",
                    description="Shell command to execute exactly as written.",
                    required=True,
                    min_length=1,
                ),
                ToolParameter(
                    name="timeout",
                    type="integer",
                    description="Execution timeout in seconds.",
                    required=False,
                    default=30,
                    minimum=1,
                    maximum=120,
                ),
            ],
        )
    )

    # emit_ui_directive
    registry.register(
        ToolContract(
            name="emit_ui_directive",
            description=(
                "Emit interactive UI blocks (forms/buttons/info cards) when user clarification is required or richer structured output helps. "
                "Always include a clear `fallback_text` for clients that cannot render directives."
            ),
            parameters=[
                ToolParameter(
                    name="directive",
                    type="object",
                    description=(
                        "UI directive payload with version, fallback_text, and blocks. "
                        "Supported block types: clarification_form, choice_buttons, info_card."
                    ),
                    required=True,
                    validator=validate_ui_directive_tool_param,
                ),
            ],
            constraints=["read_only"],
        )
    )

    # home_assistant
    registry.register(
        ToolContract(
            name="home_assistant",
            description=(
                "Home Assistant bridge. Mandatory two-step flow: call `list_tools`, then call `call_tool` with a discovered tool name. "
                "Never guess MCP tool names and prefer friendly `name` arguments over raw entity IDs."
            ),
            parameters=[
                ToolParameter(
                    name="action",
                    type="string",
                    description="`list_tools` to discover, then `call_tool` to execute a discovered MCP tool.",
                    required=True,
                    enum=["list_tools", "call_tool"],
                ),
                ToolParameter(
                    name="tool_name",
                    type="string",
                    description="MCP tool name returned by a previous `list_tools` response.",
                    required=False,
                ),
                ToolParameter(
                    name="arguments",
                    type="object",
                    description="Arguments for selected MCP tool. Most use friendly `name` fields.",
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
                "Contact directory and relationship lookup tool. Use for contact profiles, fuzzy identity search, "
                "and relationship traversal. Prefer this over generic memory search when the request is primarily about contact profiles/"
                "relationships (who someone is, how people are connected), not event-frequency ranking."
            ),
            parameters=[
                ToolParameter(
                    name="action",
                    type="string",
                    description="`search`, `get_relationships`, or `find_related` depending on lookup intent.",
                    required=True,
                    enum=["search", "get_relationships", "find_related"],
                ),
                ToolParameter(
                    name="query",
                    type="string",
                    description="Name/email/phone query for contact discovery.",
                    required=False,
                ),
                ToolParameter(
                    name="contact_id",
                    type="string",
                    description="Direct contact ID for relationship retrieval; optional when query can resolve identity.",
                    required=False,
                ),
                ToolParameter(
                    name="search_by",
                    type="string",
                    description="Field preference for `search`; default `any`.",
                    required=False,
                    enum=["name", "email", "phone", "any"],
                ),
                ToolParameter(
                    name="relationship_types",
                    type="array",
                    description="Optional relationship-type filter for `get_relationships`/`find_related`.",
                    required=False,
                    items_type="string",
                ),
                ToolParameter(
                    name="fuzzy_threshold",
                    type="integer",
                    description="Fuzzy matching threshold (0-100). Lower values allow looser matches.",
                    required=False,
                    default=75,
                    minimum=0,
                    maximum=100,
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Maximum contacts to return for search-heavy actions.",
                    required=False,
                    default=10,
                    minimum=1,
                    validator=validate_positive_int,
                ),
            ],
            constraints=["read_only"],
        )
    )

    # select_contacts - Collective selectors and reusable contact groups
    registry.register(
        ToolContract(
            name="select_contacts",
            description=(
                "Selector-based contact resolution and reusable contact-group management. "
                "Use this when the user refers to collections of people (for example domain/company/team/group) "
                "or when creating/listing/contact-group records."
                "Do not use this for pure relationship grouping (for example someone's family members), as that comes from 'resolve_contacts'."
            ),
            parameters=[
                ToolParameter(
                    name="action",
                    type="string",
                    description=(
                        "Operation to perform: `select`, `list_groups`, `get_group`, "
                        "`create_group`, or `archive_group`."
                    ),
                    required=False,
                    default="select",
                    enum=[
                        "select",
                        "list_groups",
                        "get_group",
                        "create_group",
                        "archive_group",
                    ],
                ),
                ToolParameter(
                    name="selector_kind",
                    type="string",
                    description=(
                        "Selector type for action=`select`: `email_domain`, `company`, `group`, or `tag`."
                    ),
                    required=False,
                    enum=["email_domain", "company", "group", "tag"],
                ),
                ToolParameter(
                    name="value",
                    type="string",
                    description="Selector value for action=`select`.",
                    required=False,
                ),
                ToolParameter(
                    name="auto_activate",
                    type="boolean",
                    description=(
                        "When true (default), deterministic selectors may persist reusable contact groups."
                    ),
                    required=False,
                    default=True,
                ),
                ToolParameter(
                    name="group_id",
                    type="string",
                    description="Group id for `get_group` or `archive_group`.",
                    required=False,
                ),
                ToolParameter(
                    name="name",
                    type="string",
                    description="Group name for `create_group`.",
                    required=False,
                ),
                ToolParameter(
                    name="member_contact_ids",
                    type="array",
                    description="Member contact IDs for `create_group`.",
                    required=False,
                    items_type="string",
                ),
                ToolParameter(
                    name="aliases",
                    type="array",
                    description="Optional aliases for `create_group`.",
                    required=False,
                    items_type="string",
                ),
                ToolParameter(
                    name="description",
                    type="string",
                    description="Optional description for `create_group`.",
                    required=False,
                ),
                ToolParameter(
                    name="include_archived",
                    type="boolean",
                    description="Include archived groups for `list_groups`.",
                    required=False,
                    default=False,
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Maximum contacts returned for `select`.",
                    required=False,
                    default=120,
                    minimum=1,
                    validator=validate_positive_int,
                ),
            ],
            constraints=["read_only"],
        )
    )

    # lookup_places - place name/alias retrieval and canonicalization support
    registry.register(
        ToolContract(
            name="lookup_places",
            description=(
                "Place lookup tool for matching place names/aliases in personal memory. "
                "Use this when the query is about identifying a place entity, resolving place wording, "
                "or checking if a place already exists."
            ),
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Place text to resolve (for example 'my house', 'Sao Bento').",
                    required=True,
                    min_length=1,
                ),
                ToolParameter(
                    name="near_lat",
                    type="number",
                    description="Optional latitude hint for proximity-aware ranking.",
                    required=False,
                ),
                ToolParameter(
                    name="near_lon",
                    type="number",
                    description="Optional longitude hint for proximity-aware ranking.",
                    required=False,
                ),
                ToolParameter(
                    name="fuzzy_threshold",
                    type="integer",
                    description="Minimum fuzzy score (0-100).",
                    required=False,
                    default=80,
                    minimum=0,
                    maximum=100,
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Maximum place matches to return.",
                    required=False,
                    default=5,
                    minimum=1,
                    validator=validate_positive_int,
                ),
            ],
            constraints=["read_only"],
        )
    )

    # lookup_contact_places - person-scoped place lookup (e.g., "Jordan's house")
    registry.register(
        ToolContract(
            name="lookup_contact_places",
            description=(
                "Lookup places linked to a specific contact. Useful for person-scoped references like "
                "'Jordan's house' or 'Maria's office' when contact identity is known or can be searched."
            ),
            parameters=[
                ToolParameter(
                    name="contact_id",
                    type="string",
                    description="Direct contact id to query linked places.",
                    required=False,
                ),
                ToolParameter(
                    name="contact_query",
                    type="string",
                    description="Contact search text when contact_id is not known.",
                    required=False,
                ),
                ToolParameter(
                    name="role_hint",
                    type="string",
                    description="Optional role hint like home/work/school.",
                    required=False,
                ),
                ToolParameter(
                    name="where_text",
                    type="string",
                    description="Optional original place phrase for better ranking.",
                    required=False,
                ),
            ],
            constraints=["read_only"],
        )
    )

    logger.info("[tools.registry] Registered %s tools", len(registry._contracts))
