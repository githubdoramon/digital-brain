"""System log endpoints for robot-gateway."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from auth import get_current_user
from observability.log_stream import LOG_LEVELS, get_log_buffer


def create_system_router() -> APIRouter:
    router = APIRouter()

    @router.get("/system/logs/stream")
    async def stream_system_logs(
        level: str | None = Query(default=None),
        _: dict = Depends(get_current_user),
    ):
        if level:
            normalized = level.lower()
            if normalized not in LOG_LEVELS:
                raise HTTPException(status_code=400, detail=f"Invalid log level: {level}")
            level = normalized

        buffer = get_log_buffer()

        async def event_generator():
            loop = asyncio.get_running_loop()
            last_id = 0
            heartbeat_interval_seconds = 5.0
            last_emit_at = loop.time()
            try:
                while True:
                    entries = buffer.get_since(last_id, level=level)
                    if entries:
                        for entry in entries:
                            last_id = entry.entry_id
                            yield f"data: {json.dumps(entry.to_dict(), default=str)}\n\n"
                        last_emit_at = loop.time()
                    else:
                        now = loop.time()
                        if now - last_emit_at >= heartbeat_interval_seconds:
                            yield ": keep-alive\n\n"
                            last_emit_at = now
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/system/logs")
    def list_system_logs(
        level: str | None = Query(default=None),
        since_minutes: int | None = Query(default=15, ge=1, le=1440),
        limit: int = Query(default=200, ge=1, le=1000),
        _: dict = Depends(get_current_user),
    ):
        if level:
            normalized = level.lower()
            if normalized not in LOG_LEVELS:
                raise HTTPException(status_code=400, detail=f"Invalid log level: {level}")
            level = normalized

        buffer = get_log_buffer()
        entries = buffer.get_recent(
            since_minutes=since_minutes,
            level=level,
            limit=limit,
        )
        return {"entries": [entry.to_dict() for entry in entries]}

    return router
