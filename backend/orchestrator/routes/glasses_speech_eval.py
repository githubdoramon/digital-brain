"""Explicit replay-only uploads. Never pass reference labels into inference."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import glasses_speech_eval as speech
from auth import get_current_user


def create_glasses_speech_eval_router() -> APIRouter:
    router = APIRouter(
        prefix="/mobile/glasses/speech-eval", dependencies=[Depends(get_current_user)]
    )

    @router.get("/models")
    def models():
        return {
            "enabled": speech.enabled(),
            "cpuThreadOptions": [2, 4, 8],
            "cpuEnvironment": speech.cpu_environment(),
            "models": [
                {"id": key, "repository": repo, "revision": revision, "computeType": "int8"}
                for key, (repo, revision) in speech.MODELS.items()
            ],
        }

    async def invoke(
        key: str, operation: str, cold: bool = False, audio: bytes = b"", threads: int | None = None
    ):
        try:
            return await run_in_threadpool(
                speech.engine.request, key, operation, cold, audio, threads
            )
        except speech.EvalFailure as exc:
            reason = str(exc)
            status = 429 if reason == "server_busy" else 504 if reason == "server_timeout" else 503
            if reason in {"invalid_audio", "invalid_model", "invalid_cpu_threads"}:
                status = 400
            response = {"detail": reason}
            if exc.stage != speech.EvalStage.REQUEST.value:
                response["failureStage"] = exc.stage
            if exc.http_status is not None:
                response["upstreamHttpStatus"] = exc.http_status

            return JSONResponse(response, status_code=status)

    @router.post("/prepare")
    async def prepare(
        model: str = Query(max_length=32), threads: int | None = Query(default=None, ge=1, le=8)
    ):
        return await invoke(model, "prepare", threads=threads)

    @router.post("/transcribe")
    async def transcribe(
        request: Request,
        model: str = Query(max_length=32),
        cold: bool = False,
        threads: int | None = Query(default=None, ge=1, le=8),
    ):
        if not speech.enabled():
            speech.log_rejected(model, "transcribe", "evaluation_disabled")
            raise HTTPException(503, "evaluation_disabled")
        audio = bytearray()
        async for chunk in request.stream():
            if len(audio) + len(chunk) > speech.MAX_AUDIO_BYTES:
                raise HTTPException(413, "audio_too_large")
            audio.extend(chunk)
        return await invoke(model, "transcribe", cold, bytes(audio), threads)

    return router
