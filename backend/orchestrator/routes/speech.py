"""Authenticated foreground dictation; audio/text never enter chat or command dispatch."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import speech_transcription as speech
from auth import get_current_user


def create_speech_router() -> APIRouter:
    router = APIRouter(prefix="/mobile/speech", dependencies=[Depends(get_current_user)])

    @router.post("/transcribe")
    async def transcribe(request: Request):
        audio = bytearray()
        async for chunk in request.stream():
            if len(audio) + len(chunk) > speech.MAX_DICTATION_BYTES:
                raise HTTPException(413, "audio_too_large")
            audio.extend(chunk)
        try:
            return await run_in_threadpool(speech.transcribe, bytes(audio), dictation=True)
        except speech.EvalFailure as exc:
            reason = str(exc)
            status = (
                400
                if reason == "invalid_audio"
                else 413
                if reason == "audio_too_large"
                else 429
                if reason == "server_busy"
                else 504
                if reason in {"server_timeout", "network_timeout"}
                else 503
            )
            detail = {"detail": reason, "failureStage": exc.stage}
            if exc.http_status is not None:
                detail["upstreamHttpStatus"] = exc.http_status
            return JSONResponse(detail, status_code=status)

    return router
