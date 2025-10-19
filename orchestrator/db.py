from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, List

import psycopg
from psycopg.rows import dict_row

DB_DSN = os.getenv("DB_DSN")


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    if not DB_DSN:
        raise RuntimeError("DB_DSN environment variable is not set")
    conn = psycopg.connect(DB_DSN, row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.close()


def fetch_events(ids: List[str]):
    if not ids:
        return []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id, e.ts, e.people, e.tags, e.what_text,
                   p.place_id, p.name AS place_name, p.city, p.country, p.lat, p.lon
            FROM events e
            LEFT JOIN places p ON p.place_id = e.place_id
            WHERE e.id = ANY(%s)
            """,
            (ids,),
        )
        rows = cur.fetchall()
    index = {id_: i for i, id_ in enumerate(ids)}
    rows.sort(key=lambda r: index[r["id"]])
    return rows
