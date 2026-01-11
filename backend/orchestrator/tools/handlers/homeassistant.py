"""
Home Assistant tool handler.

Handles:
- home_assistant: Control smart home devices via MCP protocol
"""

from time import perf_counter
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.state import AgentState


def _log_timing(label: str, start_time: float, **metadata: Any) -> None:
    """Log timing information for performance monitoring."""
    elapsed_ms = (perf_counter() - start_time) * 1000
    parts = [f"[timing] {label}: {elapsed_ms:.1f}ms"]
    if metadata:
        meta_str = ", ".join(f"{k}={v}" for k, v in metadata.items())
        parts.append(f"({meta_str})")
    print(" ".join(parts))


def handle_home_assistant(
    args: Dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Execute home_assistant tool.

    Controls Home Assistant devices via MCP protocol.
    Requires listing tools first before calling them.
    """
    # Lazy import to avoid circular dependencies
    from mcp import is_ha_configured, list_ha_tools, call_ha_tool

    action = args.get("action")
    if not action or action not in ("list_tools", "call_tool"):
        return {
            "error": "home_assistant requires action to be 'list_tools' or 'call_tool'"
        }

    step_start = perf_counter()

    if not is_ha_configured():
        _log_timing("tool.home_assistant", step_start, error="not_configured")
        return {
            "error": "Home Assistant MCP client not configured. Set HA_URL and HA_TOKEN environment variables.",
            "hint": "HA_URL should be your Home Assistant URL (e.g., http://homeassistant.local:8123)",
        }

    if action == "list_tools":
        print("[tool.homeassistant] home_assistant(action=list_tools)")
        tools = list_ha_tools()

        # Update state if provided
        if state is not None:
            state.add_fact(f"Listed {len(tools)} Home Assistant tools")

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
        print(
            f"[tool.homeassistant] home_assistant(action=call_tool, "
            f"tool={tool_name}, args={tool_args})"
        )

        result = call_ha_tool(tool_name, tool_args)

        # Update state if provided
        if state is not None:
            if result.get("success"):
                state.add_action(f"Called Home Assistant tool: {tool_name}")
            else:
                state.add_fact(f"Home Assistant call failed: {result.get('error')}")

        _log_timing(
            "tool.home_assistant.call_tool",
            step_start,
            tool=tool_name,
            success=result.get("success"),
        )
        return result

    return {"error": f"Unknown action: {action}"}
