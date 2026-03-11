from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

import action_logs
import immich_client
from auth import get_current_user, require_service_api_key
from observability.log_stream import LOG_LEVELS, get_log_buffer
from schemas import ServiceVersionCollection
from versioning import get_service_versions


def create_system_router() -> APIRouter:
    router = APIRouter()

    @router.get("/system/versions", response_model=ServiceVersionCollection)
    @router.get("/mobile/system/versions", response_model=ServiceVersionCollection)
    def read_service_versions(user: dict = Depends(get_current_user)):
        return get_service_versions()

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
            last_id = 0
            while True:
                entries = buffer.get_since(last_id, level=level)
                if entries:
                    for entry in entries:
                        last_id = entry.entry_id
                        yield f"data: {json.dumps(entry.to_dict(), default=str)}\\n\\n"
                await asyncio.sleep(0.5)

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

    @router.post("/access/gate")
    def validate_gate_access(
        image: UploadFile = File(...),
        _: None = Depends(require_service_api_key),
    ):
        try:
            image_bytes = image.file.read()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to read image: {exc}") from exc

        try:
            contacts, _ = immich_client.identify_contacts_from_image(
                image_bytes=image_bytes,
                filename=image.filename,
                mime_type=image.content_type,
            )
        except immich_client.ImmichClientError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except immich_client.ImmichIdentifyError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        if not contacts:
            raise HTTPException(status_code=403, detail="Not authorized")

        open_gate = False
        for contact in contacts:
            contact_name = contact.get("display_name") or contact.get("contact_id") or "unknown"
            action_logs.insert_action_log(
                action_logs.PERSON_IDENTIFIED,
                {"name": contact_name, "location": "gate"},
            )
            tags = [tag.lower() for tag in (contact.get("tags") or []) if isinstance(tag, str)]
            if "gate-access" in tags:
                open_gate = True
                action_logs.insert_action_log(
                    action_logs.LOG_TYPE_GATE_OPENED,
                    {"name": contact_name, "location": "gate"},
                )

        contact_names = ", ".join(
            [
                contact.get("display_name") or contact.get("contact_id") or "unknown"
                for contact in contacts
            ]
        )

        return {"contact_names": contact_names, "open_gate": open_gate}

    return router
