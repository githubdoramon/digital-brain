"""Bounded, ephemeral Kokoro speech through an OpenAI-compatible endpoint."""

import asyncio
import base64
import io
import os
import wave
from urllib.parse import urlsplit, urlunsplit

import httpx

MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_AUDIO_SECONDS = 45


class SpeechUnavailable(Exception):
    pass


def speech_endpoint() -> str:
    value = os.getenv("TTS_BASE_URL", "").strip()
    if not value:
        raise SpeechUnavailable("tts_not_configured")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SpeechUnavailable("tts_not_configured")
    path = parsed.path.rstrip("/")
    if not path.endswith("/audio/speech"):
        path += "/audio/speech" if path.endswith("/v1") else "/v1/audio/speech"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def pcm_payload(data: bytes) -> dict:
    try:
        if len(data) > MAX_AUDIO_BYTES:
            raise ValueError()
        with wave.open(io.BytesIO(data), "rb") as audio:
            rate, frames = audio.getframerate(), audio.getnframes()
            if (
                audio.getcomptype() != "NONE"
                or audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or rate not in {16000, 22050, 24000, 44100, 48000}
                or not 0 < frames <= rate * MAX_AUDIO_SECONDS
            ):
                raise ValueError()
            pcm = audio.readframes(frames)
            if len(pcm) != frames * 2:
                raise ValueError()
        return {
            "encoding": "pcm16",
            "sample_rate": rate,
            "data": base64.b64encode(pcm).decode("ascii"),
        }
    except (EOFError, OSError, ValueError, wave.Error) as exc:
        raise SpeechUnavailable("tts_invalid_audio") from exc


async def synthesize(text: str) -> dict:
    if not text.strip() or len(text) > 2400:
        raise SpeechUnavailable("tts_text_too_long")
    endpoint = speech_endpoint()
    headers = {"Accept": "audio/wav"}
    if key := os.getenv("TTS_API_KEY", "").strip():
        headers["Authorization"] = f"Bearer {key}"
    try:
        seconds = min(25.0, max(1.0, float(os.getenv("TTS_TIMEOUT_SECONDS", "20"))))
        async with asyncio.timeout(seconds), httpx.AsyncClient(follow_redirects=False) as client:
            async with client.stream(
                "POST",
                endpoint,
                headers=headers,
                json={
                    "model": os.getenv("TTS_MODEL", "kokoro"),
                    "voice": os.getenv("TTS_VOICE", "af_heart"),
                    "input": text,
                    "response_format": "wav",
                },
                timeout=httpx.Timeout(seconds, connect=5.0),
            ) as response:
                if response.status_code != 200:
                    raise SpeechUnavailable("tts_failed")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > MAX_AUDIO_BYTES:
                        raise SpeechUnavailable("tts_invalid_audio")
                    data.extend(chunk)
        return pcm_payload(bytes(data))
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        raise SpeechUnavailable("tts_failed") from exc
