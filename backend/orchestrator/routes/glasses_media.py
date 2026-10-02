from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import glasses_media
from auth import get_current_user


class MediaUploadIn(BaseModel):
    capture_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(gt=0, le=glasses_media.MAX_BYTES)
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str
    captured_at: str | None = None


def create_glasses_media_router() -> APIRouter:
    router = APIRouter(prefix="/mobile/glasses/media")

    @router.get("/receipts/{key}")
    def get_receipt(key: str, user: dict = Depends(get_current_user)):
        return glasses_media.receipt(user["email"], key) or {"confirmed": False}

    @router.post("/sessions")
    def create(payload: MediaUploadIn, user: dict = Depends(get_current_user)):
        try:
            return glasses_media.create_session(user["email"], payload.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.put("/sessions/{session_id}")
    async def chunk(
        session_id: str,
        request: Request,
        offset: int = Query(ge=0),
        user: dict = Depends(get_current_user),
    ):
        data = bytearray()
        async for part in request.stream():
            if len(data) + len(part) > glasses_media.CHUNK_BYTES:
                raise HTTPException(413, "Chunk exceeds one MiB")
            data.extend(part)
        try:
            return await run_in_threadpool(
                glasses_media.store_chunk, user["email"], session_id, offset, bytes(data)
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/sessions/{session_id}/complete")
    def complete(session_id: str, user: dict = Depends(get_current_user)):
        try:
            return glasses_media.complete(user["email"], session_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
