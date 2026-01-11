"""
Generic MCP (Model Context Protocol) client.

This module provides a transport-agnostic MCP client that communicates
with MCP servers using the Streamable HTTP protocol (JSON-RPC 2.0 over HTTP).

The client can be used with any MCP-compatible server, not just Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class MCPTool:
    """Represents an MCP tool definition."""
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
        }


@dataclass
class MCPToolResult:
    """Result from calling an MCP tool."""
    success: bool
    content: Any = None
    error: str | None = None
    is_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        if self.success:
            return {"success": True, "result": self.content}
        return {
            "success": False,
            "error": self.error or "Unknown error",
            "result": self.content,
        }


class MCPClientError(Exception):
    """Base exception for MCP client errors."""
    pass


class MCPAuthError(MCPClientError):
    """Authentication failed."""
    pass


class MCPConnectionError(MCPClientError):
    """Connection to MCP server failed."""
    pass


class MCPClient:
    """
    Generic MCP client using Streamable HTTP transport.

    Implements JSON-RPC 2.0 over HTTP POST requests for communicating
    with any MCP-compatible server.

    Args:
        base_url: Base URL of the MCP server (e.g., "http://localhost:8123")
        token: Authentication token (Bearer token)
        endpoint: MCP endpoint path (default: "/mcp")
        timeout: HTTP timeout in seconds (default: 30)
        client_name: Name to identify this client during initialization
        client_version: Version string for this client

    Example:
        client = MCPClient(
            base_url="http://homeassistant.local:8123",
            token="your_token_here",
            endpoint="/api/mcp",
        )
        tools = client.list_tools_sync()
        result = client.call_tool_sync("turn_on_light", {"entity_id": "light.living_room"})
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        endpoint: str = "/mcp",
        timeout: int = 30,
        client_name: str = "mcp-client",
        client_version: str = "1.0.0",
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.endpoint = endpoint
        self.timeout = timeout
        self.client_name = client_name
        self.client_version = client_version
        self._request_id = 0
        self._tools_cache: list[MCPTool] | None = None
        self._initialized = False

        if not self.base_url:
            raise MCPClientError("base_url is required")
        if not self.token:
            raise MCPClientError("token is required")

    @property
    def url(self) -> str:
        """Full URL to the MCP endpoint."""
        return f"{self.base_url}{self.endpoint}"

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers for MCP requests."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _next_request_id(self) -> int:
        """Generate next JSON-RPC request ID."""
        self._request_id += 1
        return self._request_id

    def _build_jsonrpc_request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Build a JSON-RPC 2.0 request payload."""
        request: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": method,
        }
        if params is not None:
            request["params"] = params
        return request

    def _parse_jsonrpc_response(self, response: dict[str, Any]) -> Any:
        """Parse JSON-RPC 2.0 response, handling errors."""
        if "error" in response:
            error = response["error"]
            code = error.get("code", -1)
            message = error.get("message", "Unknown error")
            data = error.get("data")
            error_msg = f"JSON-RPC error {code}: {message}"
            if data:
                error_msg += f" (data: {data})"
            raise MCPClientError(error_msg)
        return response.get("result")

    async def _send_request_async(
        self, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Send async JSON-RPC request to MCP server."""
        payload = self._build_jsonrpc_request(method, params)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    self.url,
                    json=payload,
                    headers=self._get_headers(),
                )

                if response.status_code == 401:
                    raise MCPAuthError("Authentication failed - check your token")
                if response.status_code == 403:
                    raise MCPAuthError("Access forbidden - token may lack required permissions")

                response.raise_for_status()
                return self._parse_jsonrpc_response(response.json())

            except httpx.ConnectError as e:
                raise MCPConnectionError(f"Failed to connect to {self.url}: {e}")
            except httpx.TimeoutException as e:
                raise MCPConnectionError(f"Request timeout after {self.timeout}s: {e}")

    def _send_request_sync(
        self, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Send sync JSON-RPC request to MCP server."""
        payload = self._build_jsonrpc_request(method, params)

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    self.url,
                    json=payload,
                    headers=self._get_headers(),
                )

                if response.status_code == 401:
                    raise MCPAuthError("Authentication failed - check your token")
                if response.status_code == 403:
                    raise MCPAuthError("Access forbidden - token may lack required permissions")

                response.raise_for_status()
                return self._parse_jsonrpc_response(response.json())

        except httpx.ConnectError as e:
            raise MCPConnectionError(f"Failed to connect to {self.url}: {e}")
        except httpx.TimeoutException as e:
            raise MCPConnectionError(f"Request timeout after {self.timeout}s: {e}")

    # -------------------------------------------------------------------------
    # Initialization
    # -------------------------------------------------------------------------

    async def initialize_async(self) -> dict[str, Any]:
        """
        Initialize MCP session (async).

        Returns server capabilities and info.
        """
        params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
            },
            "clientInfo": {
                "name": self.client_name,
                "version": self.client_version,
            },
        }
        result = await self._send_request_async("initialize", params)
        self._initialized = True
        return result

    def initialize_sync(self) -> dict[str, Any]:
        """
        Initialize MCP session (sync).

        Returns server capabilities and info.
        """
        params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
            },
            "clientInfo": {
                "name": self.client_name,
                "version": self.client_version,
            },
        }
        result = self._send_request_sync("initialize", params)
        self._initialized = True
        return result

    # -------------------------------------------------------------------------
    # Tools
    # -------------------------------------------------------------------------

    async def list_tools_async(self, force_refresh: bool = False) -> list[MCPTool]:
        """
        List available MCP tools (async).

        Results are cached unless force_refresh is True.
        """
        if self._tools_cache is not None and not force_refresh:
            return self._tools_cache

        result = await self._send_request_async("tools/list")
        tools = self._parse_tools_response(result)
        self._tools_cache = tools
        return tools

    def list_tools_sync(self, force_refresh: bool = False) -> list[MCPTool]:
        """
        List available MCP tools (sync).

        Results are cached unless force_refresh is True.
        """
        if self._tools_cache is not None and not force_refresh:
            return self._tools_cache

        result = self._send_request_sync("tools/list")
        tools = self._parse_tools_response(result)
        self._tools_cache = tools
        return tools

    def _parse_tools_response(self, result: dict[str, Any]) -> list[MCPTool]:
        """Parse tools/list response into MCPTool objects."""
        tools = []
        for tool_data in result.get("tools", []):
            tools.append(MCPTool(
                name=tool_data.get("name", ""),
                description=tool_data.get("description", ""),
                input_schema=tool_data.get("inputSchema", {}),
            ))
        return tools

    async def call_tool_async(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> MCPToolResult:
        """
        Call an MCP tool (async).

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments as a dictionary

        Returns:
            MCPToolResult with the tool's response
        """
        params = {
            "name": tool_name,
            "arguments": arguments or {},
        }

        try:
            result = await self._send_request_async("tools/call", params)
            return self._parse_tool_result(result)
        except MCPClientError as e:
            return MCPToolResult(
                success=False,
                error=str(e),
                is_error=True,
            )

    def call_tool_sync(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> MCPToolResult:
        """
        Call an MCP tool (sync).

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments as a dictionary

        Returns:
            MCPToolResult with the tool's response
        """
        params = {
            "name": tool_name,
            "arguments": arguments or {},
        }

        try:
            result = self._send_request_sync("tools/call", params)
            return self._parse_tool_result(result)
        except MCPClientError as e:
            return MCPToolResult(
                success=False,
                error=str(e),
                is_error=True,
            )

    def _parse_tool_result(self, result: dict[str, Any]) -> MCPToolResult:
        """Parse tools/call response into MCPToolResult."""
        # MCP returns content as a list of content blocks
        content = result.get("content", [])
        is_error = result.get("isError", False)

        # Extract text content from content blocks
        text_content = []
        for block in content:
            if block.get("type") == "text":
                text_content.append(block.get("text", ""))

        # Return single string if only one text block, otherwise list
        parsed_content = (
            text_content[0]
            if len(text_content) == 1
            else (text_content if text_content else result)
        )

        return MCPToolResult(
            success=not is_error,
            content=parsed_content,
            is_error=is_error,
        )

    # -------------------------------------------------------------------------
    # Utility methods
    # -------------------------------------------------------------------------

    def clear_cache(self) -> None:
        """Clear the tools cache."""
        self._tools_cache = None

    def get_tools_for_llm(self, prefix: str = "") -> list[dict[str, Any]]:
        """
        Get tools formatted for LLM function calling.

        Args:
            prefix: Optional prefix for tool names (e.g., "ha_" for Home Assistant)

        Returns:
            List of tool definitions in Anthropic/OpenAI format
        """
        tools = self.list_tools_sync()
        llm_tools = []

        for tool in tools:
            name = f"{prefix}{tool.name}" if prefix else tool.name
            llm_tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.description,
                    "parameters": tool.input_schema or {"type": "object", "properties": {}},
                },
            })

        return llm_tools

    def __repr__(self) -> str:
        return f"MCPClient(url={self.url!r}, client={self.client_name!r})"
