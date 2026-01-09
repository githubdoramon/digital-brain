"""
MCP (Model Context Protocol) client implementation.

This module provides a generic MCP client that can connect to any MCP server
using the Streamable HTTP transport protocol.

Usage:
    from mcp import MCPClient, get_registry

    # Direct client usage
    client = MCPClient(base_url="http://server:8080", token="...", endpoint="/mcp")
    tools = client.list_tools_sync()
    result = client.call_tool_sync("tool_name", {"arg": "value"})

    # Registry-based usage (for managing multiple MCP servers)
    registry = get_registry()
    registry.register("home_assistant", client)
    result = registry.call_tool("home_assistant", "tool_name", {"arg": "value"})
"""

from mcp.client import (
    MCPClient,
    MCPTool,
    MCPToolResult,
    MCPClientError,
    MCPAuthError,
    MCPConnectionError,
)
from mcp.registry import MCPRegistry, get_registry
from mcp.servers.home_assistant import (
    get_ha_client,
    is_ha_configured,
    list_ha_tools,
    call_ha_tool,
)

__all__ = [
    # Client
    "MCPClient",
    "MCPTool",
    "MCPToolResult",
    "MCPClientError",
    "MCPAuthError",
    "MCPConnectionError",
    # Registry
    "MCPRegistry",
    "get_registry",
    # Home Assistant shortcuts
    "get_ha_client",
    "is_ha_configured",
    "list_ha_tools",
    "call_ha_tool",
]
