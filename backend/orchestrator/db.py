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


def resolve_contact_names(
    cur: Any,
    contact_ids: list[str] | set[str],
) -> dict[str, str]:
    """Batch-resolve contact IDs to display names using an existing cursor."""
    if not contact_ids:
        return {}
    cur.execute(
        "SELECT contact_id, display_name FROM contacts WHERE contact_id = ANY(%s)",
        (list(contact_ids),),
    )
    return {row["contact_id"]: row["display_name"] for row in cur.fetchall()}


def enrich_people(
    raw_people: list[str] | None,
    contact_names: dict[str, str],
) -> list[dict[str, str]]:
    """Map raw contact ID list to [{contact_id, display_name}]."""
    if not raw_people:
        return []
    return [{"contact_id": cid, "display_name": contact_names.get(cid, cid)} for cid in raw_people]


def fetch_event_people(cur: Any, event_ids: list[str]) -> dict[str, list[str]]:
    """Fetch contact IDs per event from the event_contacts junction table."""
    if not event_ids:
        return {}
    cur.execute(
        "SELECT event_id, contact_id FROM event_contacts WHERE event_id = ANY(%s)",
        (event_ids,),
    )
    result: dict[str, list[str]] = {}
    for row in cur.fetchall():
        result.setdefault(row["event_id"], []).append(row["contact_id"])
    return result


def fetch_events(ids: list[str]):
    if not ids:
        return []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id,
                   e.start_date,
                   e.end_date,
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

        # Fetch people from junction table + resolve display names.
        people_map = fetch_event_people(cur, ids)
        all_people: set[str] = set()
        for cids in people_map.values():
            all_people.update(cids)
        contact_names = resolve_contact_names(cur, all_people)

        for r in rows:
            r["people"] = people_map.get(r["id"], [])
            r["_contact_names"] = contact_names

    index = {id_: i for i, id_ in enumerate(ids)}
    rows.sort(key=lambda r: index[r["id"]])
    return rows
