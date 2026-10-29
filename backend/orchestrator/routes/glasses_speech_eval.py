"""Authenticated GPU speech for user-enabled glasses captures and labeled replays."""

import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import speech_transcription as speech
from auth import get_current_user
from speech_timing import SpeechTimedRoute


def create_glasses_speech_eval_router() -> APIRouter:
    router = APIRouter(
        prefix="/mobile/glasses",
        dependencies=[Depends(get_current_user)],
        route_class=SpeechTimedRoute,
    )

    @router.get("/speech-eval/models")
    def models():
        return {
            "enabled": speech.enabled(),
            "models": [
                {
                    "id": model,
                    "provider": "remote",
                    "upstreamModel": upstream,
                    "coldStartControlled": False,
                }
                for model, upstream in speech.REPLAY_MODELS.items()
            ],
        }

    async def invoke(model, operation, audio=b"", threads=None, replay=True, vocabulary="none"):
        try:
            options = {"replay": replay}
            if vocabulary != "none":
                options["vocabulary"] = vocabulary
            return await run_in_threadpool(
                speech.request, model, operation, audio, threads, **options
            )
        except speech.EvalFailure as exc:
            reason = str(exc)
            status = (
                429
                if reason == "server_busy"
                else 504
                if reason in {"server_timeout", "network_timeout"}
                else 503
            )
            if reason in {
                "invalid_audio",
                "invalid_model",
                "invalid_cpu_threads",
                "invalid_vocabulary",
            }:
                status = 400
            if reason == "audio_too_large":
                status = 413
            response = {"detail": reason, "failureStage": exc.stage}
            if exc.http_status is not None:
                response["upstreamHttpStatus"] = exc.http_status
            return JSONResponse(response, status_code=status)

    async def audio_bytes(request):
        started = time.perf_counter()
        audio = bytearray()
        async for chunk in request.stream():
            if len(audio) + len(chunk) > speech.MAX_AUDIO_BYTES:
                raise HTTPException(413, "audio_too_large")
            audio.extend(chunk)
        request.state.speech_upload_ms = (time.perf_counter() - started) * 1000
        return bytes(audio)

    @router.post("/transcribe")
    async def transcribe(request: Request):
        return await invoke(
            speech.MODEL,
            "transcribe",
            await audio_bytes(request),
            replay=False,
            vocabulary="commands-v1",
        )

    @router.post("/speech-eval/prepare")
    async def prepare(
        model: str = Query(default=speech.MODEL, max_length=32),
        provider: Literal["remote"] = "remote",
        threads: int | None = None,
        vocabulary: Literal["none", "commands-v1"] = "none",
    ):
        return await invoke(model, "prepare", threads=threads, vocabulary=vocabulary)

    @router.post("/speech-eval/transcribe")
    async def replay(
        request: Request,
        model: str = Query(default=speech.MODEL, max_length=32),
        provider: Literal["remote"] = "remote",
        threads: int | None = None,
        vocabulary: Literal["none", "commands-v1"] = "none",
    ):
        return await invoke(
            model, "transcribe", await audio_bytes(request), threads=threads, vocabulary=vocabulary
        )

    return router
