"""
Home Assistant MCP server configuration.

This module provides a pre-configured MCPClient for Home Assistant's
built-in MCP server (available in HA 2025.2+).

Environment variables:
- HA_URL: Home Assistant URL (e.g., http://homeassistant.local:8123)
- HA_TOKEN: Long-lived access token for authentication
- HA_MCP_TIMEOUT: HTTP timeout in seconds (default: 30)

The HA MCP server exposes tools based on what's configured in
Home Assistant's "Exposed Entities" settings.
"""

from __future__ import annotations

import os
from time import perf_counter
from typing import Any

from mcp.client import MCPClient, MCPClientError

# Configuration from environment
HA_URL = os.getenv("HA_URL", "").rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN", "")
HA_MCP_TIMEOUT = int(os.getenv("HA_MCP_TIMEOUT", "30"))

# HA-specific MCP endpoint
HA_MCP_ENDPOINT = "/api/mcp"

# Singleton client instance
_client: MCPClient | None = None


def is_ha_configured() -> bool:
    """Check if Home Assistant MCP client is properly configured."""
    return bool(HA_URL and HA_TOKEN)


def get_ha_client() -> MCPClient:
    """
    Get or create the Home Assistant MCP client singleton.

    Returns:
        Configured MCPClient instance for Home Assistant

    Raises:
        MCPClientError: If HA_URL or HA_TOKEN are not set
    """
    global _client

    if _client is None:
        if not HA_URL:
            raise MCPClientError("HA_URL environment variable is required")
        if not HA_TOKEN:
            raise MCPClientError("HA_TOKEN environment variable is required")

        _client = MCPClient(
            base_url=HA_URL,
            token=HA_TOKEN,
            endpoint=HA_MCP_ENDPOINT,
            timeout=HA_MCP_TIMEOUT,
            client_name="digital-brain-orchestrator",
            client_version="1.0.0",
        )

    return _client


def reset_client() -> None:
    """Reset the client singleton (useful for testing or reconfiguration)."""
    global _client
    _client = None


# -------------------------------------------------------------------------
# Convenience functions for direct use
# -------------------------------------------------------------------------


def list_ha_tools(force_refresh: bool = False) -> list[dict[str, Any]]:
    """
    List available Home Assistant MCP tools.

    Returns:
        List of tool definitions suitable for LLM consumption
    """
    if not is_ha_configured():
        return []

    try:
        client = get_ha_client()
        tools = client.list_tools_sync(force_refresh=force_refresh)
        return [tool.to_dict() for tool in tools]
    except MCPClientError as e:
        print(f"[mcp.ha] Failed to list tools: {e}")
        return []


def call_ha_tool(
    tool_name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Call a Home Assistant MCP tool.

    Args:
        tool_name: Name of the tool to call
        arguments: Tool arguments

    Returns:
        Dict with 'success', 'result' or 'error' keys
    """
    if not is_ha_configured():
        return {
            "success": False,
            "error": "MCP client not configured - set HA_URL and HA_TOKEN",
        }

    start = perf_counter()

    try:
        client = get_ha_client()
        result = client.call_tool_sync(tool_name, arguments)
        elapsed = perf_counter() - start

        print(f"[mcp.ha] call_ha_tool({tool_name}) completed in {elapsed:.3f}s")

        return result.to_dict()

    except MCPClientError as e:
        elapsed = perf_counter() - start
        print(f"[mcp.ha] call_ha_tool({tool_name}) failed in {elapsed:.3f}s: {e}")
        return {
            "success": False,
            "error": str(e),
        }


async def call_ha_tool_async(
    tool_name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Call a Home Assistant MCP tool (async version).

    Args:
        tool_name: Name of the tool to call
        arguments: Tool arguments

    Returns:
        Dict with 'success', 'result' or 'error' keys
    """
    if not is_ha_configured():
        return {
            "success": False,
            "error": "MCP client not configured - set HA_URL and HA_TOKEN",
        }

    start = perf_counter()

    try:
        client = get_ha_client()
        result = await client.call_tool_async(tool_name, arguments)
        elapsed = perf_counter() - start

        print(f"[mcp.ha] call_ha_tool_async({tool_name}) completed in {elapsed:.3f}s")

        return result.to_dict()

    except MCPClientError as e:
        elapsed = perf_counter() - start
        print(f"[mcp.ha] call_ha_tool_async({tool_name}) failed in {elapsed:.3f}s: {e}")
        return {
            "success": False,
            "error": str(e),
        }
