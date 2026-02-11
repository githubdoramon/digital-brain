"""
Home Assistant tool handler.

Handles:
- home_assistant: Control smart home devices via MCP protocol
"""

import os
import sys
from time import perf_counter
from typing import TYPE_CHECKING, Any, Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from observability import trace
from tools.action_enums import HomeAssistantAction

if TYPE_CHECKING:
    from agent.state import AgentState


def _detect_common_mistakes(
    tool_name: str,
    tool_args: dict[str, Any],
    state: Optional["AgentState"],
) -> Optional[str]:
    """
    Detect common mistakes in Home Assistant tool usage.

    Returns a hint string if a likely mistake is detected, None otherwise.
    """
    # Check if using entity_id instead of name
    if "entity_id" in tool_args and "name" not in tool_args:
        return (
            "You used 'entity_id' but Home Assistant MCP tools expect 'name' "
            "with the device's friendly name (e.g., 'office lights', not 'light.office'). "
            "Use 'name' parameter instead."
        )

    # Check if using HassLightSet when user goal suggests turn off
    if state and tool_name == "HassLightSet":
        goal_lower = state.goal.lower()
        turn_off_keywords = ["turn off", "switch off", "shut off", "lights off", "off the"]
        if any(kw in goal_lower for kw in turn_off_keywords):
            # Check if brightness is 0 (acceptable) or not set / high
            brightness = tool_args.get("brightness")
            if brightness is None or brightness > 0:
                return (
                    "The user wants to TURN OFF the device, but you used HassLightSet. "
                    "To turn off a device, use 'HassTurnOff' tool with arguments={'name': 'device name'}. "
                    "HassLightSet is for adjusting brightness/color, not for turning off."
                )

    # Check if using HassLightSet when user goal suggests turn on
    if state and tool_name == "HassLightSet":
        goal_lower = state.goal.lower()
        turn_on_keywords = ["turn on", "switch on", "lights on", "on the light"]
        if any(kw in goal_lower for kw in turn_on_keywords):
            # If only brightness is set (no name), it's probably wrong usage
            if "brightness" in tool_args and len(tool_args) <= 2:
                return (
                    "The user wants to TURN ON the device. Use 'HassTurnOn' with "
                    "arguments={'name': 'device name'}. HassLightSet is for adjusting "
                    "properties after a light is already on."
                )

    return None


def handle_home_assistant(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> dict[str, Any]:
    """
    Execute home_assistant tool.

    Controls Home Assistant devices via MCP protocol.
    Requires listing tools first before calling them.
    """
    # Lazy import to avoid circular dependencies
    from mcp import call_ha_tool, is_ha_configured, list_ha_tools

    action = str(args.get("action") or "").strip()
    parsed_action = HomeAssistantAction.from_value(action)
    if parsed_action is None:
        return {"error": "home_assistant requires action to be 'list_tools' or 'call_tool'"}

    step_start = perf_counter()

    if not is_ha_configured():
        trace.trace_tool_error("home_assistant", "Home Assistant MCP client not configured")
        return {
            "error": "Home Assistant MCP client not configured. Set HA_URL and HA_TOKEN environment variables.",
            "hint": "HA_URL should be your Home Assistant URL (e.g., http://homeassistant.local:8123)",
        }

    if parsed_action is HomeAssistantAction.LIST_TOOLS:
        tools = list_ha_tools()
        duration_ms = (perf_counter() - step_start) * 1000
        trace.trace_ha_list_tools(len(tools), duration_ms)

        # Update state if provided
        if state is not None:
            state.add_fact(f"Listed {len(tools)} Home Assistant tools")
        return {
            "tools": tools,
            "count": len(tools),
            "action_mapping": {
                "turn_off": "HassTurnOff - use 'name' parameter with device friendly name (e.g., 'office lights')",
                "turn_on": "HassTurnOn - use 'name' parameter with device friendly name",
                "set_brightness": "HassLightSet - use 'name' and 'brightness' (0-100) parameters",
                "set_color": "HassLightSet - use 'name' and 'rgb_color' or 'color_temp' parameters",
                "set_temperature": "HassClimateSetTemperature - use 'name' and 'temperature' parameters",
            },
            "usage_guide": (
                "CRITICAL ACTION MAPPING - Read carefully:\n"
                "- To TURN OFF a device: Use 'HassTurnOff' with arguments={'name': 'device name'}\n"
                "- To TURN ON a device: Use 'HassTurnOn' with arguments={'name': 'device name'}\n"
                "- To SET BRIGHTNESS: Use 'HassLightSet' with arguments={'name': 'device name', 'brightness': 0-100}\n"
                "- To SET COLOR: Use 'HassLightSet' with arguments={'name': 'device name', 'rgb_color': [r,g,b]}\n"
                "\n"
                "COMMON MISTAKES TO AVOID:\n"
                "- Do NOT use HassLightSet to turn off - use HassTurnOff instead\n"
                "- Do NOT use entity_id - use 'name' with the friendly name (e.g., 'office lights', 'bedroom lamp')\n"
                "- Do NOT guess entity IDs like 'light.living_room' - use friendly names\n"
                "\n"
                "The 'name' parameter accepts friendly device names as shown in Home Assistant UI."
            ),
            "hint": "Use action='call_tool' with tool_name set to one of the above tools and arguments matching its inputSchema.",
        }

    if parsed_action is HomeAssistantAction.CALL_TOOL:
        tool_name = args.get("tool_name")
        if not tool_name or not isinstance(tool_name, str):
            return {"error": "call_tool action requires a tool_name string"}

        tool_args = args.get("arguments") or {}

        # Detect common mistakes before calling
        mistake_hint = _detect_common_mistakes(tool_name, tool_args, state)
        if mistake_hint:
            trace.trace_ha_mistake_detected("Incorrect tool usage", mistake_hint)
            return {
                "success": False,
                "error": "Likely incorrect tool usage detected",
                "hint": mistake_hint,
                "suggestion": "Please review the action_mapping from list_tools and try again with the correct tool.",
            }

        result = call_ha_tool(tool_name, tool_args)
        duration_ms = (perf_counter() - step_start) * 1000
        trace.trace_ha_call_tool(tool_name, tool_args, result.get("success", False), duration_ms)

        # Update state if provided
        if state is not None:
            if result.get("success"):
                state.add_action(f"Called Home Assistant tool: {tool_name}")
            else:
                state.add_fact(f"Home Assistant call failed: {result.get('error')}")

        # If the tool call failed, provide helpful guidance
        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            # Check if this looks like a hallucinated tool name
            if "not found" in error_msg.lower() or "unknown" in error_msg.lower():
                result["hint"] = (
                    f"Tool '{tool_name}' was not found. You MUST call home_assistant with "
                    "action='list_tools' FIRST to get the actual available tool names. "
                    "Do NOT guess tool names - they are specific to this Home Assistant installation."
                )
            else:
                result["hint"] = (
                    "The tool call failed. Check the error message and try again with corrected arguments. "
                    "If unsure about available tools or their arguments, call action='list_tools' first."
                )

        return result

    return {"error": f"Unknown action: {action}"}
