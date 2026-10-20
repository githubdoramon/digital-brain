"""Shared knowledge API, through the existing authenticated mobile proxy."""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

import knowledge
import knowledge_jobs
from auth import get_current_user


class Correction(BaseModel):
    statement: str = Field(min_length=3, max_length=2000)
    category: str | None = None


def create_knowledge_router():
    def private(response: Response):
        response.headers["Cache-Control"] = "no-store"

    router = APIRouter(
        prefix="/mobile/knowledge", tags=["knowledge"], dependencies=[Depends(private)]
    )

    @router.get("")
    def listing(
        query: str | None = None,
        subject: str | None = None,
        limit: int = Query(40, ge=1, le=100),
        offset: int = Query(0, ge=0),
        profile: bool = False,
        user=Depends(get_current_user),
    ):
        return {
            "knowledge": knowledge.list_items(
                user["email"],
                query=query,
                subject=subject,
                limit=limit,
                offset=offset,
                profile=profile,
            )
        }

    @router.get("/entities")
    def entities(query: str | None = None, user=Depends(get_current_user)):
        return {"entities": knowledge.entities_for(user["email"], query=query)}

    @router.get("/summary")
    def summary(subject: str, user=Depends(get_current_user)):
        return knowledge.summary_for(user["email"], subject)

    @router.get("/progress")
    def progress(user=Depends(get_current_user)):
        return knowledge_jobs.progress(user["email"])

    @router.post("/retry")
    def retry(user=Depends(get_current_user)):
        return knowledge_jobs.retry_failed(user["email"])

    @router.patch("/{knowledge_id}")
    def correct(knowledge_id: str, payload: Correction, user=Depends(get_current_user)):
        if payload.category is not None and payload.category not in knowledge.CATEGORIES:
            raise HTTPException(422, "Unknown category")
        row = knowledge.change(
            user["email"], knowledge_id, statement=payload.statement, category=payload.category
        )
        if not row:
            raise HTTPException(404, "Knowledge not found")
        return {"knowledge": row}

    @router.delete("/{knowledge_id}", status_code=204)
    def remove(knowledge_id: str, user=Depends(get_current_user)):
        if not knowledge.change(user["email"], knowledge_id, delete=True):
            raise HTTPException(404, "Knowledge not found")
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    return router
