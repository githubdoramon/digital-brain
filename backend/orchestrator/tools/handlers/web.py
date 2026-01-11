"""
Web-related tool handlers.

Handles:
- web_search: Search the web for external information
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


def handle_web_search(
    args: Dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> Dict[str, Any]:
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

    print(f"[tool.web] web_search(query={query!r}, max_results={max_results})")
    step_start = perf_counter()

    search_result = web_tools.internet_search(query, max_results=max_results)
    results = search_result.get("results", [])

    # Update state if provided
    if state is not None and results:
        state.add_fact(f"Web search for '{query}' returned {len(results)} results")

    _log_timing("tool.web_search", step_start, results=len(results))
    return {"results": results, "count": len(results)}
