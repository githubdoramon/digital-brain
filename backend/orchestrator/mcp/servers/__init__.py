"""
MCP server configurations.

This package contains configuration modules for specific MCP servers.
Each module provides a factory function to create a configured MCPClient
instance for that server.
"""

from mcp.servers.home_assistant import (
    get_ha_client,
    is_ha_configured,
    list_ha_tools,
    call_ha_tool,
)

__all__ = [
    "get_ha_client",
    "is_ha_configured",
    "list_ha_tools",
    "call_ha_tool",
]
