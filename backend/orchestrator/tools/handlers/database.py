"""
Database-related tool handlers.

Handles:
- execute_sql: Run read-only SQL queries
- describe_schema: Get database schema information

Note: Tracing/logging is handled at the controller level via the centralized
trace module. Handlers focus purely on execution logic.
"""

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agent.state import AgentState


def _normalize_sql_result(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize SQL results, converting date objects to ISO strings."""
    if "rows" in result and isinstance(result["rows"], list):
        result["rows"] = [_json_safe(row) for row in result["rows"]]
    return result


def _json_safe(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a row dict to JSON-safe format."""
    safe: dict[str, Any] = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            safe[key] = value.isoformat()
        else:
            safe[key] = value
    return safe


def handle_execute_sql(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> dict[str, Any]:
    """
    Execute execute_sql tool.

    Runs a read-only SQL query on the user's database.
    """
    # Lazy import to avoid circular dependencies
    import sql_tools

    sql = args.get("sql", "")

    if not sql:
        return {"error": "sql is required"}

    result = sql_tools.execute_sql(sql)
    result = _normalize_sql_result(result)

    row_count = len(result.get("rows", [])) if "rows" in result else 0

    # Update state if provided
    if state is not None and row_count > 0:
        state.add_fact(f"SQL query returned {row_count} rows")

    return result


def handle_describe_schema(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> dict[str, Any]:
    """
    Execute describe_schema tool.

    Returns the database schema with tables, columns, and types.
    """
    # Lazy import to avoid circular dependencies
    import sql_tools

    schema = sql_tools.describe_schema()

    # Update state if provided
    if state is not None:
        state.add_fact("Retrieved database schema")

    return {"schema": schema}
