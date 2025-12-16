from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

from psycopg.types.json import Json

from db import get_conn

LOG_TYPE_GATE_OPENED = "gate_opened"
PERSON_IDENTIFIED = "person_identified"
_ALLOWED_TYPES = {LOG_TYPE_GATE_OPENED, PERSON_IDENTIFIED}


def insert_action_log(log_type: str, raw: Dict[str, Any]) -> str:
    if log_type not in _ALLOWED_TYPES:
        raise ValueError(f"Unsupported log_type: {log_type}")

    log_id = f"action_{uuid4().hex}"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO action_logs (id, log_type, raw)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (log_id, log_type, Json(raw)),
        )
        row = cur.fetchone()
        conn.commit()

    return row["id"] if row else log_id

