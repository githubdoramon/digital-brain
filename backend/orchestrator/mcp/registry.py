"""
MCP Server Registry.

This module provides a registry for managing multiple MCP server connections.
It allows registering, listing, and interacting with multiple MCP servers
from a single interface.

Usage:
    from mcp import get_registry

    registry = get_registry()

    # Register servers
    registry.register("home_assistant", ha_client)
    registry.register("memory", memory_client)

    # List all tools across servers
    all_tools = registry.list_all_tools()

    # Call a tool on a specific server
    result = registry.call_tool("home_assistant", "turn_on", {"entity_id": "light.living_room"})
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from mcp.client import MCPClient, MCPClientError, MCPTool

logger = logging.getLogger(__name__)


@dataclass
class RegisteredServer:
    """A registered MCP server with metadata."""

    name: str
    client: MCPClient
    description: str = ""
    enabled: bool = True
    call_count: int = 0
    last_error: str | None = None


class MCPRegistry:
    """
    Registry for managing multiple MCP server connections.

    Thread-safe registry that allows registering multiple MCP servers
    and provides unified access to their tools.
    """

    def __init__(self):
        self._servers: dict[str, RegisteredServer] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        client: MCPClient,
        description: str = "",
        enabled: bool = True,
    ) -> None:
        """
        Register an MCP server.

        Args:
            name: Unique name for this server
            client: Configured MCPClient instance
            description: Human-readable description
            enabled: Whether the server is enabled (default: True)
        """
        with self._lock:
            self._servers[name] = RegisteredServer(
                name=name,
                client=client,
                description=description,
                enabled=enabled,
            )
            logger.info("[mcp.registry] Registered server: %s", name)

    def unregister(self, name: str) -> bool:
        """
        Unregister an MCP server.

        Args:
            name: Name of the server to unregister

        Returns:
            True if server was unregistered, False if not found
        """
        with self._lock:
            if name in self._servers:
                del self._servers[name]
                logger.info("[mcp.registry] Unregistered server: %s", name)
                return True
            return False

    def get_server(self, name: str) -> RegisteredServer | None:
        """Get a registered server by name."""
        with self._lock:
            return self._servers.get(name)

    def get_client(self, name: str) -> MCPClient | None:
        """Get the client for a registered server."""
        server = self.get_server(name)
        return server.client if server else None

    def list_servers(self) -> list[dict[str, Any]]:
        """
        List all registered servers.

        Returns:
            List of server info dictionaries
        """
        with self._lock:
            return [
                {
                    "name": server.name,
                    "description": server.description,
                    "enabled": server.enabled,
                    "url": server.client.url,
                    "call_count": server.call_count,
                    "last_error": server.last_error,
                }
                for server in self._servers.values()
            ]

    def list_tools(self, server_name: str, force_refresh: bool = False) -> list[MCPTool]:
        """
        List tools from a specific server.

        Args:
            server_name: Name of the registered server
            force_refresh: Force refresh from server (ignore cache)

        Returns:
            List of MCPTool objects
        """
        server = self.get_server(server_name)
        if not server:
            raise MCPClientError(f"Server '{server_name}' not found")
        if not server.enabled:
            raise MCPClientError(f"Server '{server_name}' is disabled")

        return server.client.list_tools_sync(force_refresh=force_refresh)

    def list_all_tools(self, force_refresh: bool = False) -> dict[str, list[MCPTool]]:
        """
        List tools from all enabled servers.

        Args:
            force_refresh: Force refresh from all servers

        Returns:
            Dict mapping server names to their tool lists
        """
        result: dict[str, list[MCPTool]] = {}

        with self._lock:
            for name, server in self._servers.items():
                if not server.enabled:
                    continue
                try:
                    result[name] = server.client.list_tools_sync(force_refresh=force_refresh)
                except MCPClientError as e:
                    logger.warning(
                        "[mcp.registry] Failed to list tools from %s: %s",
                        name,
                        e,
                        exc_info=e,
                    )
                    server.last_error = str(e)
                    result[name] = []

        return result

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Call a tool on a specific server.

        Args:
            server_name: Name of the registered server
            tool_name: Name of the tool to call
            arguments: Tool arguments

        Returns:
            Dict with 'success', 'result' or 'error' keys
        """
        server = self.get_server(server_name)
        if not server:
            return {"success": False, "error": f"Server '{server_name}' not found"}
        if not server.enabled:
            return {"success": False, "error": f"Server '{server_name}' is disabled"}

        start = perf_counter()

        try:
            result = server.client.call_tool_sync(tool_name, arguments)

            with self._lock:
                server.call_count += 1
                server.last_error = None

            elapsed = perf_counter() - start
            logger.info(
                "[mcp.registry] %s.%s() completed in %.3fs",
                server_name,
                tool_name,
                elapsed,
            )

            return result.to_dict()

        except MCPClientError as e:
            with self._lock:
                server.last_error = str(e)

            elapsed = perf_counter() - start
            logger.warning(
                "[mcp.registry] %s.%s() failed in %.3fs: %s",
                server_name,
                tool_name,
                elapsed,
                e,
                exc_info=e,
            )

            return {"success": False, "error": str(e)}

    async def call_tool_async(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Call a tool on a specific server (async).

        Args:
            server_name: Name of the registered server
            tool_name: Name of the tool to call
            arguments: Tool arguments

        Returns:
            Dict with 'success', 'result' or 'error' keys
        """
        server = self.get_server(server_name)
        if not server:
            return {"success": False, "error": f"Server '{server_name}' not found"}
        if not server.enabled:
            return {"success": False, "error": f"Server '{server_name}' is disabled"}

        start = perf_counter()

        try:
            result = await server.client.call_tool_async(tool_name, arguments)

            with self._lock:
                server.call_count += 1
                server.last_error = None

            elapsed = perf_counter() - start
            logger.info(
                "[mcp.registry] %s.%s() completed in %.3fs",
                server_name,
                tool_name,
                elapsed,
            )

            return result.to_dict()

        except MCPClientError as e:
            with self._lock:
                server.last_error = str(e)

            elapsed = perf_counter() - start
            logger.warning(
                "[mcp.registry] %s.%s() failed in %.3fs: %s",
                server_name,
                tool_name,
                elapsed,
                e,
                exc_info=e,
            )

            return {"success": False, "error": str(e)}

    def set_enabled(self, server_name: str, enabled: bool) -> bool:
        """
        Enable or disable a server.

        Args:
            server_name: Name of the server
            enabled: Whether to enable or disable

        Returns:
            True if server was found and updated
        """
        server = self.get_server(server_name)
        if server:
            server.enabled = enabled
            logger.info(
                "[mcp.registry] Server %s %s",
                server_name,
                "enabled" if enabled else "disabled",
            )
            return True
        return False

    def get_stats(self) -> dict[str, Any]:
        """Get registry statistics."""
        with self._lock:
            total_calls = sum(s.call_count for s in self._servers.values())
            enabled_count = sum(1 for s in self._servers.values() if s.enabled)

            return {
                "total_servers": len(self._servers),
                "enabled_servers": enabled_count,
                "total_calls": total_calls,
                "servers": {
                    name: {
                        "enabled": s.enabled,
                        "call_count": s.call_count,
                        "last_error": s.last_error,
                    }
                    for name, s in self._servers.items()
                },
            }


# Module-level singleton
_registry: MCPRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> MCPRegistry:
    """Get or create the singleton MCP registry."""
    global _registry

    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = MCPRegistry()

    return _registry


def reset_registry() -> None:
    """Reset the registry singleton (useful for testing)."""
    global _registry
    with _registry_lock:
        _registry = None
