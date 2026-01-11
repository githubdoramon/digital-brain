"""
MCP server configurations.

This package contains configuration modules for specific MCP servers.
Each module provides a factory function to create a configured MCPClient
instance for that server.
"""

from mcp.servers.home_assistant import (
    call_ha_tool,
    get_ha_client,
    is_ha_configured,
    list_ha_tools,
)

__all__ = [
    "get_ha_client",
    "is_ha_configured",
    "list_ha_tools",
    "call_ha_tool",
]
