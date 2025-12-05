from __future__ import annotations

import re
from typing import Any, Dict

from psycopg.rows import dict_row

from db import POSTGRES_SCHEMA, get_conn

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

_INCOMPLETE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in [
        r"\bwhere\s*$",
        r"\band\s*$",
        r"\bor\s*$",
        r"\bon\s*$",
        r"\bjoin\s*$",
        r"\bin\s*$",
        r"\blike\s*$",
        r"\bbetween\s*$",
        r"=\s*$",
        r">\s*$",
        r"<\s*$",
        r"!=\s*$",
        r">=\s*$",
        r"<=\s*$",
        r"\bis\s*$",
        r"\bis\s+not\s*$",
        r",\s*$",
    ]
]

_AGGREGATE_UNCLOSED_PATTERNS = [
    re.compile(rf"\b{name}\s*\([^\)]*$", re.IGNORECASE | re.DOTALL)
    for name in ["count", "sum", "avg", "min", "max"]
]


def describe_schema() -> Dict[str, Any]:
    schema = POSTGRES_SCHEMA or "public"
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
            WHERE table_schema = %s
            ORDER BY table_name, ordinal_position
            """,
            (schema,),
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
    # Instead of substring matching, use word-boundary regex to catch forbidden statements (like UPDATE, DELETE)
    for forbidden in _READ_ONLY_FORBIDDEN:
        pattern = rf"\b{forbidden}\b"
        if re.search(pattern, lowered):
            raise ValueError(f"Found forbidden statement '{forbidden}' in query")
    for pattern in _INCOMPLETE_PATTERNS:
        if pattern.search(cleaned):
            raise ValueError("SQL query appears incomplete or ends with a dangling clause")
    for pattern in _AGGREGATE_UNCLOSED_PATTERNS:
        if pattern.search(cleaned):
            raise ValueError("SQL query appears to contain an unterminated aggregate function call")
    return cleaned
