"""Glasses speech as a modality on the regular, timed main chat session."""

import asyncio
import re
import time
from contextlib import suppress
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from auth import get_current_user
from glasses_chat_receipts import claim
from glasses_tts import SpeechUnavailable, synthesize
from routes.chat import ask
from schemas import AskIn, ClientContextIn
from search_normalization import normalize_search_text
from time_context import use_time_context
from voice_context import spoken_reply, voice_progress, voice_stage

_active_owners: set[str] = set()
CHAT_DEADLINE_SECONDS = 300
SPEECH_TEST_DEADLINE_SECONDS = 80
_wake = re.compile(r"^\W*([^\W\d_]+)\W+([^\W\d_]+)\b[\s,.:;!?-]*(.*)$", re.S)


class GlassesChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transcript: str = Field(min_length=1, max_length=8192)
    request_id: UUID
    captured_at_ms: int = Field(gt=0)
    timezone: str = Field(default="UTC", max_length=100)


class GlassesSpeechTestIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=400)


def chat_phrase(transcript: str) -> str:
    match = _wake.fullmatch(transcript.strip())
    if (
        not match
        or normalize_search_text(match[1]) not in {"hey", "okay", "ok"}
        or normalize_search_text(match[2]) not in {"brain", "brane", "bring", "brian"}
        or not re.search(r"\w", match[3])
    ):
        raise HTTPException(422, "invalid_wake_or_empty_phrase")
    phrase = match[3].strip()
    # Preserve the established hands-free session reset.
    return re.sub(r"^slash\s+new\b", "/new", phrase, flags=re.I)


async def _voice_background_tasks(tasks: BackgroundTasks):
    with spoken_reply():
        await tasks()


def create_glasses_chat_router() -> APIRouter:
    router = APIRouter(prefix="/mobile/glasses/chat")

    @router.post("/speech-test")
    async def speech_test(
        payload: GlassesSpeechTestIn, request: Request, response: Response,
        user: dict = Depends(get_current_user),
    ):
        """Explicit foreground TTS test; never creates chat or learning evidence."""
        response.headers["Cache-Control"] = "no-store"
        owner = user["email"]
        if owner in _active_owners or len(_active_owners) >= 8:
            raise HTTPException(429, "voice_chat_busy")
        _active_owners.add(owner)

        async def run():
            with spoken_reply():
                try:
                    audio = await synthesize(payload.text)
                    voice_stage("speech_ready")
                    return {"audio": audio, "audio_error": None}
                except SpeechUnavailable as exc:
                    voice_stage("speech_failed", str(exc))
                    return {"audio": None, "audio_error": str(exc)}

        task = None
        try:
            task = asyncio.create_task(run())
            async with asyncio.timeout(SPEECH_TEST_DEADLINE_SECONDS):
                while not task.done():
                    if await request.is_disconnected():
                        raise HTTPException(499, "request_cancelled")
                    await asyncio.wait({task}, timeout=0.2)
                return await task
        except TimeoutError as exc:
            raise HTTPException(504, "speech_test_timeout") from exc
        finally:
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            _active_owners.discard(owner)

    @router.post("")
    async def chat(
        payload: GlassesChatIn,
        request: Request,
        response: Response,
        background_tasks: BackgroundTasks,
        user: dict = Depends(get_current_user),
    ):
        response.headers["Cache-Control"] = "no-store"
        phrase = chat_phrase(payload.transcript)
        if not -10_000 <= time.time() * 1000 - payload.captured_at_ms <= 90_000:
            raise HTTPException(409, "capture_expired")
        owner = user["email"]
        if owner in _active_owners or len(_active_owners) >= 8:
            raise HTTPException(429, "voice_chat_busy")
        _active_owners.add(owner)
        task = None
        try:
            if not await run_in_threadpool(claim, owner, payload.request_id):
                raise HTTPException(409, "request_already_claimed")
            voice_stage("started")

            async def run():
                clock = {
                    "sent_at": datetime.fromtimestamp(
                        payload.captured_at_ms / 1000, timezone.utc
                    ).isoformat(),
                    "timezone": payload.timezone,
                }
                with spoken_reply(), use_time_context(clock):
                    voice_tasks = BackgroundTasks()
                    chat_started = time.perf_counter()
                    result = await ask(
                        AskIn(
                            question=phrase,
                            client_context=ClientContextIn(timezone=payload.timezone),
                        ),
                        voice_tasks,
                        user,
                    )
                    background_tasks.add_task(_voice_background_tasks, voice_tasks)
                    chat_ms = (time.perf_counter() - chat_started) * 1000
                    voice_progress("chat_complete", duration_ms=chat_ms)
                    voice_stage("answer_ready")
                    audio, audio_error = None, None
                    tts_started = time.perf_counter()
                    try:
                        audio = await synthesize(result.answer)
                        voice_stage("speech_ready")
                    except SpeechUnavailable as exc:
                        audio_error = str(exc)
                        voice_stage("speech_failed", audio_error)
                    tts_ms = (time.perf_counter() - tts_started) * 1000
                    voice_progress("tts_complete", duration_ms=tts_ms, success=audio is not None)
                    response.headers["Server-Timing"] = f"voice_chat;dur={chat_ms:.1f}, voice_tts;dur={tts_ms:.1f}"
                    return {
                        "answer": result.answer,
                        "thread_id": result.thread_id,
                        "is_new_session": result.is_new_session,
                        "audio": audio,
                        "audio_error": audio_error,
                    }

            task = asyncio.create_task(run())
            async with asyncio.timeout(CHAT_DEADLINE_SECONDS):
                while not task.done():
                    if await request.is_disconnected():
                        voice_stage("cancelled")
                        raise HTTPException(499, "request_cancelled")
                    await asyncio.wait({task}, timeout=0.2)
                return await task
        except TimeoutError as exc:
            voice_stage("timeout")
            raise HTTPException(504, "voice_chat_timeout") from exc
        finally:
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            _active_owners.discard(owner)

    return router
