from __future__ import annotations

from contextlib import contextmanager
from typing import Any, cast

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from config import POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_SCHEMA
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

DB_DSN = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

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
    conn = psycopg.connect(DB_DSN, row_factory=cast(Any, dict_row))
    _set_search_path(conn)
    try:
        yield conn
    finally:
        conn.close()
