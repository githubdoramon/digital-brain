from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, cast

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

# Construct DB connection string from environment variables
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_DB = os.getenv("POSTGRES_DB")
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_PORT = os.getenv("POSTGRES_PORT")
POSTGRES_SCHEMA = (os.getenv("POSTGRES_SCHEMA") or "public").strip()

DB_DSN = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

# Log schema configuration at startup
logger.info("[db] Configured POSTGRES_SCHEMA=%r", POSTGRES_SCHEMA)


def _set_search_path(conn: psycopg.Connection) -> None:
    if not POSTGRES_SCHEMA:
        logger.warning("[db] POSTGRES_SCHEMA is empty, using default search_path")
        return
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(POSTGRES_SCHEMA))
        )


@contextmanager
def get_conn():
    if not POSTGRES_PASSWORD:
        raise RuntimeError("POSTGRES_PASSWORD environment variable is not set")
    conn = psycopg.connect(DB_DSN, row_factory=cast(Any, dict_row))  # type: ignore[arg-type]
    _set_search_path(conn)
    try:
        yield conn
    finally:
        conn.close()


def fetch_events(ids: list[str]):
    if not ids:
        return []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id,
                   e.start_date,
                   e.end_date,
                   e.people,
                   e.tags,
                   e.types,
                   e.title,
                   e.summary,
                   e.external_id,
                   p.place_id, p.name AS place_name, p.city, p.country, p.lat, p.lon
            FROM events e
            LEFT JOIN places p ON p.place_id = e.place_id
            WHERE e.id = ANY(%s)
            """,
            (ids,),
        )
        rows: list[dict[str, Any]] = [dict(row) for row in cur.fetchall()]
    index = {id_: i for i, id_ in enumerate(ids)}
    rows.sort(key=lambda r: index[r["id"]])
    return rows
