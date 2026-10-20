from fastapi import APIRouter, Depends, HTTPException, Query, Response

import intelligence
import messaging_archive
from auth import get_current_user
from db import get_conn


def create_intelligence_router():
    def private_response(response: Response):
        response.headers["Cache-Control"] = "no-store"

    router = APIRouter(
        prefix="/mobile/intelligence",
        tags=["intelligence"],
        dependencies=[Depends(private_response)],
    )

    @router.get("/projects")
    def projects(user=Depends(get_current_user)):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM intelligence_projects WHERE user_email=%s ORDER BY created_at DESC LIMIT 100",
                (user["email"],),
            )
            return {"projects": [dict(r) for r in cur.fetchall()]}

    @router.get("/sources/{source_id}")
    def source(
        source_id: int,
        part: int = Query(default=0, ge=0, le=1000000),
        user=Depends(get_current_user),
    ):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM intelligence_sources WHERE source_id=%s
                AND (owner_email IS NULL OR owner_email=%s)""",
                (source_id, user["email"]),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Source not found")
            actor = None
            if row["kind"] == "message":
                cur.execute(
                    """SELECT i.display_name FROM messaging_identities i
                    JOIN messaging_accounts a USING(account_id)
                    WHERE i.identity_id=%s AND a.user_email=%s""",
                    (row["payload"].get("identity_id"), user["email"]),
                )
                identity = cur.fetchone()
                actor = (
                    "You"
                    if row["payload"].get("outgoing")
                    else (identity or {}).get("display_name")
                )
            return {
                "kind": row["kind"],
                "source_link": messaging_archive.message_link(user["email"], row["entity_id"])
                if row["kind"] == "message"
                else None,
                "entity_id": row["entity_id"],
                "current": intelligence._current(cur, row, user["email"]),
                "speaker": actor,
                **row["payload"],
                "text": row["payload"].get("text", "")[
                    part * intelligence.CHUNK_SIZE : (part + 1) * intelligence.CHUNK_SIZE + 400
                ],
            }

    @router.get("/status")
    def status(user=Depends(get_current_user)):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT started_at, seeded, last_success_at FROM intelligence_state WHERE user_email=%s",
                (user["email"],),
            )
            state = cur.fetchone()
            cur.execute(
                """SELECT slot, status, batches, error_code FROM intelligence_runs
                WHERE user_email=%s ORDER BY cutoff DESC LIMIT 8""",
                (user["email"],),
            )
            runs = [dict(r) for r in cur.fetchall()]
            cur.execute(
                """SELECT COUNT(*) FILTER (WHERE next_attempt_at IS NOT NULL) AS retrying,
                COUNT(*) FILTER (WHERE next_attempt_at IS NULL) AS paused
                FROM intelligence_failures WHERE user_email=%s""",
                (user["email"],),
            )
            failures = dict(cur.fetchone())
        return {
            "schedule": ["05:00", "19:00"],
            "timezone": "Europe/Lisbon",
            "state": state,
            "runs": runs,
            "failures": failures,
        }

    @router.post("/retry-failures")
    def retry_failures(user=Depends(get_current_user)):
        return {"retrying": intelligence.retry_failures(user["email"])}

    return router
