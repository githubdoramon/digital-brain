from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

import intelligence
from auth import get_current_user
from db import get_conn


class KnowledgeCorrection(BaseModel):
    statement: str = Field(min_length=3, max_length=2000)


def create_intelligence_router():
    def private_response(response: Response):
        response.headers["Cache-Control"] = "no-store"

    router = APIRouter(
        prefix="/mobile/intelligence",
        tags=["intelligence"],
        dependencies=[Depends(private_response)],
    )

    @router.get("/knowledge")
    def knowledge(
        contact_id: str | None = None, query: str | None = None, user=Depends(get_current_user)
    ):
        return {
            "knowledge": intelligence.knowledge_for(
                user["email"], contact_id=contact_id, query=query
            )
        }

    @router.patch("/knowledge/{knowledge_id}")
    def correct(knowledge_id: str, payload: KnowledgeCorrection, user=Depends(get_current_user)):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM intelligence_knowledge WHERE user_email=%s AND knowledge_id=%s FOR UPDATE",
                (user["email"], knowledge_id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Knowledge not found")
            cur.execute(
                "INSERT INTO intelligence_knowledge_revisions(knowledge_id, snapshot) VALUES (%s,%s)",
                (knowledge_id, intelligence.json_value(dict(row))),
            )
            cur.execute(
                """UPDATE intelligence_knowledge SET statement=%s, manual=TRUE,
                status='current', applicable_at=NULL, valid_from=NULL, valid_until=NULL,
                updated_at=NOW() WHERE knowledge_id=%s""",
                (payload.statement, knowledge_id),
            )
            conn.commit()
        return {"ok": True}

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
        return {
            "schedule": ["05:00", "19:00"],
            "timezone": "Europe/Lisbon",
            "state": state,
            "runs": runs,
        }

    return router
