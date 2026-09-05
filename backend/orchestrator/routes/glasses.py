"""Authenticated smart-glasses command and ephemeral audio routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import Response as FastAPIResponse

from auth import get_current_user
from glasses_audio import delete_audio, get_audio
from glasses_commands import GlassesCommandError, process_command
from schemas import GlassesCommandIn, GlassesCommandOut


def create_glasses_router() -> APIRouter:
    router = APIRouter()

    @router.post("/mobile/glasses/commands", response_model=GlassesCommandOut)
    async def submit_glasses_command(
        payload: GlassesCommandIn,
        user: dict = Depends(get_current_user),
    ):
        try:
            return await process_command(payload, user)
        except GlassesCommandError as exc:
            raise HTTPException(
                status_code=503 if exc.retryable else 400,
                detail={"code": exc.code, "message": exc.message, "retryable": exc.retryable},
            ) from exc

    @router.get("/mobile/glasses/audio/{audio_id}")
    def download_glasses_audio(audio_id: str, user: dict = Depends(get_current_user)) -> Response:
        # Authentication is intentionally required even though IDs are opaque.
        user_email = str(user.get("email") or user.get("user_email") or "").strip()
        data = get_audio(audio_id, user_email=user_email)
        if data is None:
            raise HTTPException(status_code=404, detail="Audio not found or expired")
        # Background deletion runs only after a successful response has been
        # handed to Starlette, preserving retry-free one-shot semantics.
        from starlette.background import BackgroundTask

        return FastAPIResponse(
            content=data,
            media_type="audio/wav",
            headers={"Content-Disposition": f'inline; filename="{audio_id}.wav"'},
            background=BackgroundTask(delete_audio, audio_id),
        )

    return router
