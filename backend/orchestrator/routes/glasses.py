"""Authenticated smart-glasses command and ephemeral audio routes."""

from __future__ import annotations

import time

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import Response as FastAPIResponse

from auth import get_current_user
from glasses_audio import delete_audio, get_audio
from glasses_commands import GlassesCommandError, process_command
from observability.logger import get_runtime_logger
from schemas import GlassesCommandIn, GlassesCommandOut

logger = get_runtime_logger(__name__)


def create_glasses_router() -> APIRouter:
    router = APIRouter()

    @router.post("/mobile/glasses/commands", response_model=GlassesCommandOut)
    async def submit_glasses_command(
        payload: GlassesCommandIn,
        request: Request,
        authorization: str | None = Header(None),
    ):
        route_started_at = time.perf_counter()
        auth_duration_ms = -1.0
        command_id = str(payload.command_id)
        header_command_id = request.headers.get("x-glasses-command-id", "")
        logger.info(
            "[glasses] command route received command_id=%s header_command_id=%s",
            command_id,
            header_command_id,
        )
        outcome = "exception"
        try:
            auth_started_at = time.perf_counter()
            try:
                user = await get_current_user(authorization)
            except HTTPException as exc:
                outcome = f"auth_http_{exc.status_code}"
                logger.info(
                    "[glasses] command authentication failed command_id=%s status=%d auth_ms=%.1f",
                    command_id,
                    exc.status_code,
                    (time.perf_counter() - auth_started_at) * 1_000,
                )
                raise
            finally:
                auth_duration_ms = (time.perf_counter() - auth_started_at) * 1_000
            logger.info(
                "[glasses] command authenticated command_id=%s auth_ms=%.1f",
                command_id,
                auth_duration_ms,
            )
            result = await process_command(payload, user)
            outcome = str(result.get("outcome") or "unknown")
            return result
        except GlassesCommandError as exc:
            outcome = exc.code
            raise HTTPException(
                status_code=503 if exc.retryable else 400,
                detail={"code": exc.code, "message": exc.message, "retryable": exc.retryable},
            ) from exc
        finally:
            logger.info(
                "[glasses] command route completed command_id=%s header_command_id=%s "
                "outcome=%s auth_ms=%.1f route_ms=%.1f",
                command_id,
                header_command_id,
                outcome,
                auth_duration_ms,
                (time.perf_counter() - route_started_at) * 1_000,
            )

    @router.get("/mobile/glasses/audio/{audio_id}")
    async def download_glasses_audio(
        audio_id: str,
        request: Request,
        authorization: str | None = Header(None),
    ) -> Response:
        started_at = time.perf_counter()
        command_id = request.headers.get("x-glasses-command-id", "")
        auth_started_at = time.perf_counter()
        try:
            user = await get_current_user(authorization)
        except HTTPException as exc:
            logger.info(
                "[glasses] audio download authentication failed command_id=%s status=%d auth_ms=%.1f",
                command_id,
                exc.status_code,
                (time.perf_counter() - auth_started_at) * 1_000,
            )
            raise
        auth_duration_ms = (time.perf_counter() - auth_started_at) * 1_000
        # Authentication is intentionally required even though IDs are opaque.
        user_email = str(user.get("email") or user.get("user_email") or "").strip()
        data = get_audio(audio_id, user_email=user_email)
        if data is None:
            logger.info(
                "[glasses] audio download command_id=%s found=false auth_ms=%.1f duration_ms=%.1f",
                command_id,
                auth_duration_ms,
                (time.perf_counter() - started_at) * 1_000,
            )
            raise HTTPException(status_code=404, detail="Audio not found or expired")
        logger.info(
            "[glasses] audio download command_id=%s found=true size_bytes=%d auth_ms=%.1f duration_ms=%.1f",
            command_id,
            len(data),
            auth_duration_ms,
            (time.perf_counter() - started_at) * 1_000,
        )
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
