"""
Web-related tool handlers.

Handles:
- web_search: Search the web for external information

Note: Tracing/logging is handled at the controller level via the centralized
trace module. Handlers focus purely on execution logic.
"""

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agent.state import AgentState


def handle_web_search(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> dict[str, Any]:
    """
    Execute web_search tool.

    Searches the web for current information.
    """
    # Lazy import to avoid circular dependencies
    import web_tools

    query = args.get("query", "")
    max_results = args.get("max_results", 5)

    if not query:
        return {"error": "query is required", "results": [], "count": 0}

    search_result = web_tools.internet_search(query, max_results=max_results)
    results = search_result.get("results", [])

    # Update state if provided
    if state is not None and results:
        state.add_fact(f"Web search for '{query}' returned {len(results)} results")

    return {"results": results, "count": len(results)}
