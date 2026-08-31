from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from psycopg.types.json import Jsonb

from db import get_conn
from schemas import MomentIn


class MomentConflictError(ValueError):
    pass


@dataclass(frozen=True)
class MomentWriteResult:
    status: Literal["created", "updated", "duplicate"]
    moment: dict[str, Any]


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _serialize_moment(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "id": str(row["id"]),
        "observed_at": row["observed_at"].isoformat(),
        "received_at": row["received_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def upsert_moment(
    *,
    user_email: str,
    moment: MomentIn,
    location: dict[str, Any] | None,
    place_id: str | None = None,
) -> MomentWriteResult:
    observation = moment.observation.model_dump(mode="json")
    normalized_location = location or {}

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT user_email FROM moments WHERE id = %s", (moment.id,))
        owner = cur.fetchone()
        if owner and owner["user_email"] != user_email:
            raise MomentConflictError("moment id belongs to another user")

        cur.execute(
            """
            SELECT id, user_email, source_type, observed_at, observed_timezone,
                   observed_utc_offset_minutes, schema_version, observation, location,
                   place_id, received_at, updated_at
            FROM moments
            WHERE id = %s AND user_email = %s
            """,
            (moment.id, user_email),
        )
        existing = cur.fetchone()
        if existing:
            existing_dict = dict(existing)
            if (
                existing_dict["source_type"] != moment.source_type
                or existing_dict["observed_at"] != moment.observed_at
            ):
                raise MomentConflictError("moment id conflicts with immutable source or observed time")

            unchanged = (
                existing_dict["observed_timezone"] == moment.observed_timezone
                and existing_dict["observed_utc_offset_minutes"] == moment.observed_utc_offset_minutes
                and existing_dict["schema_version"] == moment.observation.schema_version
                and _canonical_json(existing_dict["observation"]) == _canonical_json(observation)
                and _canonical_json(existing_dict["location"] or {}) == _canonical_json(normalized_location)
                and existing_dict["place_id"] == place_id
            )
            if unchanged:
                return MomentWriteResult("duplicate", _serialize_moment(existing_dict))

            cur.execute(
                """
                UPDATE moments
                SET observed_timezone = %s,
                    observed_utc_offset_minutes = %s,
                    schema_version = %s,
                    observation = %s,
                    location = %s,
                    place_id = %s,
                    updated_at = NOW()
                WHERE id = %s AND user_email = %s
                RETURNING id, user_email, source_type, observed_at, observed_timezone,
                          observed_utc_offset_minutes, schema_version, observation, location,
                          place_id, received_at, updated_at
                """,
                (
                    moment.observed_timezone,
                    moment.observed_utc_offset_minutes,
                    moment.observation.schema_version,
                    Jsonb(observation),
                    Jsonb(normalized_location),
                    place_id,
                    moment.id,
                    user_email,
                ),
            )
            row = dict(cur.fetchone())
            conn.commit()
            return MomentWriteResult("updated", _serialize_moment(row))

        cur.execute(
            """
            INSERT INTO moments (
                id, user_email, source_type, observed_at, observed_timezone,
                observed_utc_offset_minutes, schema_version, observation, location, place_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, user_email, source_type, observed_at, observed_timezone,
                      observed_utc_offset_minutes, schema_version, observation, location,
                      place_id, received_at, updated_at
            """,
            (
                moment.id,
                user_email,
                moment.source_type,
                moment.observed_at,
                moment.observed_timezone,
                moment.observed_utc_offset_minutes,
                moment.observation.schema_version,
                Jsonb(observation),
                Jsonb(normalized_location),
                place_id,
            ),
        )
        row = dict(cur.fetchone())
        conn.commit()
        return MomentWriteResult("created", _serialize_moment(row))


def list_moments(
    *,
    user_email: str,
    source_type: str | None = None,
    observed_after: datetime | None = None,
    observed_before: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    clauses = ["user_email = %s"]
    params: list[Any] = [user_email]
    if source_type:
        clauses.append("source_type = %s")
        params.append(source_type)
    if observed_after:
        clauses.append("observed_at >= %s")
        params.append(observed_after)
    if observed_before:
        clauses.append("observed_at <= %s")
        params.append(observed_before)
    where = " AND ".join(clauses)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS count FROM moments WHERE {where}", params)
        total = int(cur.fetchone()["count"])
        cur.execute(
            f"""
            SELECT id, user_email, source_type, observed_at, observed_timezone,
                   observed_utc_offset_minutes, schema_version, observation, location,
                   place_id, received_at, updated_at
            FROM moments WHERE {where}
            ORDER BY observed_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            [*params, limit, offset],
        )
        items = [_serialize_moment(dict(row)) for row in cur.fetchall()]
    return {"moments": items, "total": total, "limit": limit, "offset": offset}


def get_moment(*, user_email: str, moment_id: str) -> dict[str, Any] | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, user_email, source_type, observed_at, observed_timezone,
                   observed_utc_offset_minutes, schema_version, observation, location,
                   place_id, received_at, updated_at
            FROM moments WHERE id = %s AND user_email = %s
            """,
            (moment_id, user_email),
        )
        row = cur.fetchone()
    return _serialize_moment(dict(row)) if row else None
