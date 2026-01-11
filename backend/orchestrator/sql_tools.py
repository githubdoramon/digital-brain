from __future__ import annotations

import re
from typing import Any

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

_SCHEMA_HINT_CACHE: str | None = None
_SCHEMA_SNAPSHOT: dict[str, Any] | None = None

_TABLE_REF_REGEX = re.compile(r"\b(?:from|join|into)\s+([a-zA-Z_][\w.]*)", re.IGNORECASE)


def describe_schema() -> dict[str, Any]:
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

    tables: dict[str, dict[str, Any]] = {}
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


def load_schema_hint() -> str:
    """Return a human-readable summary of tables and columns, cached per process."""
    global _SCHEMA_HINT_CACHE
    if _SCHEMA_HINT_CACHE is not None:
        return _SCHEMA_HINT_CACHE

    snapshot = get_schema_snapshot()
    if not snapshot:
        _SCHEMA_HINT_CACHE = ""
        return ""

    lines: list[str] = [
        "Database schema snapshot (read-only):",
    ]
    for table_name in sorted(snapshot):
        columns = snapshot[table_name].get("columns") or []
        column_bits: list[str] = []
        for col in columns:
            name = col.get("name")
            dtype = col.get("type")
            if name and dtype:
                column_bits.append(f"{name} ({dtype})")
            elif name:
                column_bits.append(name)
        if len(column_bits) > 6:
            summary = ", ".join(column_bits[:6]) + ", ..."
        else:
            summary = ", ".join(column_bits)
        lines.append(f"- {table_name}: {summary}")
    lines.append("Use describe_schema for full details and execute_sql to pull rows.")

    hint = "\n".join(lines)
    _SCHEMA_HINT_CACHE = hint
    return hint


def get_schema_snapshot() -> dict[str, Any]:
    """Return cached schema snapshot keyed by table name."""
    global _SCHEMA_SNAPSHOT
    if _SCHEMA_SNAPSHOT is not None:
        return _SCHEMA_SNAPSHOT

    try:
        schema_snapshot = describe_schema()
    except Exception:
        _SCHEMA_SNAPSHOT = {}
        return {}

    tables = schema_snapshot.get("tables") or {}
    _SCHEMA_SNAPSHOT = tables
    return tables


def find_unknown_tables(query: str) -> set[str]:
    snapshot = get_schema_snapshot()
    if not snapshot:
        return set()
    known_tables = {name.lower() for name in snapshot.keys()}
    referenced = extract_table_names(query)
    return {table for table in referenced if table not in known_tables}


def extract_table_names(query: str) -> set[str]:
    tables: set[str] = set()
    if not query:
        return tables
    for match in _TABLE_REF_REGEX.findall(query):
        candidate = match.strip()
        if not candidate:
            continue
        # Strip aliasing or quoting remnants
        candidate = candidate.strip('"')
        if " " in candidate:
            candidate = candidate.split(" ")[0]
        if "," in candidate:
            candidate = candidate.split(",")[0]
        if "." in candidate:
            candidate = candidate.split(".")[-1]
        lowered = candidate.lower()
        if lowered and lowered not in {"select", "from", "join", "unnest"}:
            tables.add(lowered)
    return tables


def execute_sql(query: str, limit: int = 200) -> dict[str, Any]:
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
