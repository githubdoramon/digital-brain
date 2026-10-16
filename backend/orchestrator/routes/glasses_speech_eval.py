"""Explicit replay-only uploads. Never pass reference labels into inference."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
            "models": [
                {"id": key, "repository": repo, "revision": revision, "computeType": "int8"}
                for key, (repo, revision) in speech.MODELS.items()
            ],
        }

    async def invoke(key: str, operation: str, cold: bool = False, audio: bytes = b""):
        try:
            return await run_in_threadpool(speech.engine.request, key, operation, cold, audio)
        except speech.EvalFailure as exc:
            reason = str(exc)
            status = 429 if reason == "server_busy" else 504 if reason == "server_timeout" else 503
            if reason in {"invalid_audio", "invalid_model"}:
                status = 400
            raise HTTPException(status, reason) from None

    @router.post("/prepare")
    async def prepare(model: str = Query(max_length=32)):
        return await invoke(model, "prepare")

    @router.post("/transcribe")
    async def transcribe(request: Request, model: str = Query(max_length=32), cold: bool = False):
        if not speech.enabled():
            raise HTTPException(503, "evaluation_disabled")
        audio = bytearray()
        async for chunk in request.stream():
            if len(audio) + len(chunk) > speech.MAX_AUDIO_BYTES:
                raise HTTPException(413, "audio_too_large")
            audio.extend(chunk)
        return await invoke(model, "transcribe", cold, bytes(audio))

    return router
