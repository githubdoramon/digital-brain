"""
Tool definitions and handlers for the LLM agent.

This module contains:
- TOOLS: The function call definitions for Ollama
- _handle_tool_call: Dispatcher for tool execution
"""

import json
from time import perf_counter
from typing import Any

import bash_tools

# Import tool implementations
import contacts
import documents as documents_service
import events as events_service
import retrieval
import skills
import sql_tools
import web_tools
from mcp import call_ha_tool, is_ha_configured, list_ha_tools


def _log_timing(label: str, start_time: float, **metadata: Any) -> None:
    """Log timing information for performance monitoring."""
    elapsed_ms = (perf_counter() - start_time) * 1000
    parts = [f"[timing] {label}: {elapsed_ms:.1f}ms"]
    if metadata:
        meta_str = ", ".join(f"{k}={v}" for k, v in metadata.items())
        parts.append(f"({meta_str})")
    print(" ".join(parts))


# Tool definitions for Ollama function calling
TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_memories",
            "description": (
                "Perform semantic (vector) search over the user's memories, including events, moments, "
                "documents, and other recorded information. Use this for exploratory queries when you "
                "need to find relevant information based on meaning rather than exact matches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A natural-language search phrase describing what to find.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "Maximum results to return (default 5).",
                    },
                    "time_start": {
                        "type": "string",
                        "description": "ISO 8601 timestamp for the earliest results (optional).",
                    },
                    "time_end": {
                        "type": "string",
                        "description": "ISO 8601 timestamp for the latest results (optional).",
                    },
                    "contact_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by contact IDs (optional).",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by tags - can be major tags (e.g., 'Health') or minor tags (e.g., 'prescription'). Multiple tags use OR logic.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_sql",
            "description": (
                "Run a read-only SQL query on the user's personal database. Use for precise queries "
                "when you know the schema. Always validate column names with describe_schema first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A valid PostgreSQL SELECT statement.",
                    },
                },
                "required": ["sql"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_schema",
            "description": (
                "Get the database schema with table names, columns, and types. "
                "Call this before writing SQL to validate column names."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_query",
            "description": (
                "Extract structured entities from a natural-language query: contacts (people), "
                "places, and time ranges. Returns IDs you can use in other tool calls."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The user's natural-language query to parse.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_events",
            "description": (
                "Retrieve full details for specific events by their IDs. Use after search_memories "
                "to get complete event information including attendees, location, and full content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of event IDs to retrieve.",
                    },
                },
                "required": ["event_ids"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_document",
            "description": (
                "Retrieve full content of a document by its ID. Use after search_memories "
                "to get the complete document text and metadata."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "string",
                        "description": "The document ID to retrieve.",
                    },
                },
                "required": ["document_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current information. Use when the user asks about "
                "external topics, news, or information not in their personal database."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Maximum results to return (default 5).",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_skill_script",
            "description": (
                "Execute a script from an active skill. Only available when a skill with scripts "
                "is active. Check activated skills for available scripts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "Name of the active skill containing the script.",
                    },
                    "script_name": {
                        "type": "string",
                        "description": "Name of the script file to execute (e.g., 'generate.py').",
                    },
                    "args": {
                        "type": "object",
                        "description": "Arguments to pass to the script as JSON.",
                    },
                },
                "required": ["skill_name", "script_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a shell command and return stdout, stderr, and exit code. Use this for CLI operations like curl, jq, file manipulation, or any system command. Commands run in a sandboxed environment with timeout protection.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute (e.g., 'curl -s https://api.example.com/data | jq .name').",
                    },
                    "timeout": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 120,
                        "description": "Maximum execution time in seconds (default 30, max 120).",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "home_assistant",
            "description": (
                "Control Home Assistant smart home/office devices via MCP protocol. "
                "CRITICAL: You MUST call with action='list_tools' FIRST to discover available tools - "
                "NEVER guess tool names or entity IDs. Tool names and entity IDs vary per installation. "
                "Only after listing tools can you use action='call_tool' with a valid tool_name from the list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list_tools", "call_tool"],
                        "description": "Action to perform: 'list_tools' MUST be called first to discover available tools. Only use 'call_tool' after you have the tool list.",
                    },
                    "tool_name": {
                        "type": "string",
                        "description": "Name of the MCP tool to call - MUST be a tool name returned by 'list_tools'. Required when action is 'call_tool'.",
                    },
                    "arguments": {
                        "type": "object",
                        "description": "Arguments to pass to the MCP tool (when action is 'call_tool').",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    },
]


class AgentState:
    """Tracks state across tool calls within a single agent loop."""

    def __init__(self) -> None:
        self.resolution: dict[str, Any] = {}
        self.search_results: list[dict[str, Any]] = []
        self.detailed_events: list[dict[str, Any]] = []
        self.activated_skills: list[dict[str, Any]] = []

    def to_metadata(self) -> dict[str, Any]:
        """Convert state to metadata for storage."""
        return {
            "resolution": self.resolution,
            "search_results_count": len(self.search_results),
            "detailed_events_count": len(self.detailed_events),
            "activated_skills": [s.get("name") for s in self.activated_skills],
        }


def _normalize_sql_result(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize SQL results, converting date objects to ISO strings."""
    if "rows" in result and isinstance(result["rows"], list):
        result["rows"] = [_json_safe(row) for row in result["rows"]]
    return result


def _json_safe(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a row dict to JSON-safe format."""
    safe: dict[str, Any] = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            safe[key] = value.isoformat()
        else:
            safe[key] = value
    return safe


def handle_tool_call(
    tool_call: dict[str, Any],
    state: AgentState,
    question: str,
    search_limit: int,
) -> dict[str, Any]:
    """
    Execute a tool call and return the result.

    Args:
        tool_call: The tool call dict from Ollama
        state: Current agent state to update
        question: The original user question
        search_limit: Default limit for search results

    Returns:
        Dict with the tool result
    """
    function = tool_call.get("function") or {}
    name = function.get("name")
    raw_args = function.get("arguments") or "{}"

    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except json.JSONDecodeError:
        return {"error": f"Invalid JSON arguments: {raw_args}"}

    if name == "search_memories":
        query = args.get("query", question)
        limit = args.get("limit", search_limit)
        time_start = args.get("time_start")
        time_end = args.get("time_end")
        contact_ids = args.get("contact_ids")  # Maps to 'people' parameter
        # Note: 'tags' from tool schema is not yet supported by retrieval.search_memories

        print(f"[agent] calling search_memories(query={query!r}, limit={limit}, time_start={time_start}, time_end={time_end}, contact_ids={contact_ids})")
        step_start = perf_counter()

        search_result = retrieval.search_memories(
            query,
            people=contact_ids,
            time_start=time_start,
            time_end=time_end,
            limit=limit,
        )
        results = search_result.get("results", [])
        state.search_results.extend(results)

        _log_timing("tool.search_memories", step_start, results=len(results))
        return {"results": results, "count": len(results)}

    if name == "execute_sql":
        sql = args.get("sql", "")
        print(f"[agent] calling execute_sql(sql={sql!r})")
        step_start = perf_counter()

        result = sql_tools.execute_sql(sql)
        result = _normalize_sql_result(result)

        _log_timing(
            "tool.execute_sql",
            step_start,
            rows=len(result.get("rows", [])) if "rows" in result else 0,
        )
        return result

    if name == "describe_schema":
        print("[agent] calling describe_schema()")
        step_start = perf_counter()

        schema = sql_tools.describe_schema()

        _log_timing("tool.describe_schema", step_start)
        return {"schema": schema}

    if name == "resolve_query":
        query = args.get("query", question)
        print(f"[agent] calling resolve_query(query={query!r})")
        step_start = perf_counter()

        resolution = contacts.resolve_query(query)
        state.resolution = resolution

        _log_timing(
            "tool.resolve_query",
            step_start,
            contacts=len(resolution.get("contacts", [])),
            places=len(resolution.get("places", [])),
        )
        return resolution

    if name == "get_events":
        event_ids = args.get("event_ids", [])
        print(f"[agent] calling get_events(event_ids={event_ids})")
        step_start = perf_counter()

        events = events_service.get_events(event_ids) if event_ids else []
        state.detailed_events.extend(events)

        _log_timing("tool.get_events", step_start, found=len(events), requested=len(event_ids))
        return {"events": events, "count": len(events)}

    if name == "get_document":
        document_id = args.get("document_id")
        if not document_id:
            return {"error": "document_id is required"}

        print(f"[agent] calling get_document(document_id={document_id!r})")
        step_start = perf_counter()

        document = documents_service.get_document(document_id)

        _log_timing("tool.get_document", step_start, found=document is not None)

        if document:
            return {"document": document}
        return {"error": f"Document not found: {document_id}"}

    if name == "web_search":
        query = args.get("query", "")
        max_results = args.get("max_results", 5)
        print(f"[agent] calling web_search(query={query!r}, max_results={max_results})")
        step_start = perf_counter()

        search_result = web_tools.internet_search(query, max_results=max_results)
        results = search_result.get("results", [])

        _log_timing("tool.web_search", step_start, results=len(results))
        return {"results": results, "count": len(results)}

    if name == "run_skill_script":
        skill_name = args.get("skill_name")
        script_name = args.get("script_name")
        script_args = args.get("args") or {}

        if not skill_name or not isinstance(skill_name, str):
            return {"error": "run_skill_script requires a skill_name string"}
        if not script_name or not isinstance(script_name, str):
            return {"error": "run_skill_script requires a script_name string"}

        # Verify the skill is active
        active_skill_names = [s.get("name") for s in state.activated_skills]
        if skill_name not in active_skill_names:
            return {
                "error": f"Skill '{skill_name}' is not active. Active skills: {active_skill_names}"
            }

        # Get skill and run script
        registry = skills.get_registry()
        skill = registry.get_skill(skill_name)
        if not skill:
            return {"error": f"Skill '{skill_name}' not found in registry"}

        print(f"[agent] calling run_skill_script skill={skill_name}, script={script_name}, args={script_args}")
        step_start = perf_counter()

        runner = skills.get_runner_for_skill(skill.path)
        result = runner.run_script(script_name, script_args)

        _log_timing(
            "tool.run_skill_script",
            step_start,
            skill=skill_name,
            script=script_name,
            returncode=result.get("returncode"),
        )
        return result

    if name == "bash":
        command = args.get("command")
        if not command or not isinstance(command, str):
            return {"error": "bash requires a command string"}

        timeout_arg = args.get("timeout")
        try:
            timeout = int(timeout_arg) if timeout_arg is not None else None
        except (TypeError, ValueError):
            timeout = None

        print(f"[agent] calling bash(command={command!r}, timeout={timeout})")
        step_start = perf_counter()

        result = bash_tools.execute_bash(command, timeout=timeout)

        _log_timing(
            "tool.bash",
            step_start,
            returncode=result.get("returncode"),
            stdout_len=len(result.get("stdout", "")),
            stderr_len=len(result.get("stderr", "")),
        )
        return result

    if name == "home_assistant":
        action = args.get("action")
        if not action or action not in ("list_tools", "call_tool"):
            return {"error": "home_assistant requires action to be 'list_tools' or 'call_tool'"}

        step_start = perf_counter()

        if not is_ha_configured():
            _log_timing("tool.home_assistant", step_start, error="not_configured")
            return {
                "error": "Home Assistant MCP client not configured. Set HA_URL and HA_TOKEN environment variables.",
                "hint": "HA_URL should be your Home Assistant URL (e.g., http://homeassistant.local:8123)",
            }

        if action == "list_tools":
            print("[agent] calling home_assistant(action=list_tools)")
            tools = list_ha_tools()
            _log_timing("tool.home_assistant.list_tools", step_start, tool_count=len(tools))
            return {
                "tools": tools,
                "count": len(tools),
                "hint": "Use action='call_tool' with tool_name and arguments to execute a tool.",
            }

        if action == "call_tool":
            tool_name = args.get("tool_name")
            if not tool_name or not isinstance(tool_name, str):
                return {"error": "call_tool action requires a tool_name string"}

            tool_args = args.get("arguments") or {}
            print(f"[agent] calling home_assistant(action=call_tool, tool={tool_name}, args={tool_args})")

            result = call_ha_tool(tool_name, tool_args)

            _log_timing(
                "tool.home_assistant.call_tool",
                step_start,
                tool=tool_name,
                success=result.get("success"),
            )
            return result

    raise RuntimeError(f"Unsupported tool requested: {name}")
