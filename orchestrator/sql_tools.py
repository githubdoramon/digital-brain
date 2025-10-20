from __future__ import annotations

from typing import Any, Dict, List, Sequence

from psycopg.rows import dict_row

from db import get_conn

_READ_ONLY_FORBIDDEN = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "grant",
    "revoke",
    "truncate",
}


def describe_schema() -> Dict[str, Any]:
    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                table_name,
                column_name,
                data_type,
                is_nullable,
                column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
            """
        )
        columns = cur.fetchall()

    tables: Dict[str, Dict[str, Any]] = {}
    for col in columns:
        table = col["table_name"]
        tables.setdefault(table, {"columns": []})
        tables[table]["columns"].append(
            {
                "name": col["column_name"],
                "type": col["data_type"],
                "nullable": col["is_nullable"] == "YES",
                "default": col["column_default"],
            }
        )
    return {"tables": tables}


def execute_sql(query: str, limit: int = 200) -> Dict[str, Any]:
    clean_query = _ensure_read_only(query)
    limit = max(1, min(limit, 1000))

    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(clean_query)
        rows = cur.fetchmany(limit)

    return {
        "query": clean_query,
        "rowcount": len(rows),
        "rows": rows,
    }


def _ensure_read_only(query: str) -> str:
    if not query:
        raise ValueError("SQL query must not be empty")
    cleaned = query.strip().rstrip(";")
    lowered = cleaned.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise ValueError("Only SELECT queries (including CTEs) are allowed")
    if ";" in cleaned:
        raise ValueError("Multiple statements are not allowed")
    for forbidden in _READ_ONLY_FORBIDDEN:
        if forbidden in lowered:
            raise ValueError(f"Found forbidden statement '{forbidden}' in query")
    return cleaned
