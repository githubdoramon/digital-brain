"""Bounded, ephemeral Kokoro speech through an OpenAI-compatible endpoint."""

import asyncio
import base64
import io
import os
import struct
import wave
from urllib.parse import urlsplit, urlunsplit

import httpx

from voice_context import voice_stage

MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_AUDIO_SECONDS = 45


class SpeechUnavailable(Exception):
    pass


def _streaming_pcm_size(data: bytes) -> int | None:
    """Recognize explicit unknown-length RIFF/data headers, not ordinary truncation."""
    unknown = {0xFFFFFFFF, 0x7FFFFFFF}
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    if struct.unpack_from("<I", data, 4)[0] not in unknown:
        return None
    offset = 12
    while offset + 8 <= len(data):
        chunk = data[offset : offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        offset += 8
        if chunk == b"data":
            return len(data) - offset if size in unknown else None
        if size in unknown or offset + size > len(data):
            return None
        offset += size + size % 2
    return None


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
            ):
                raise ValueError()
            # Read only the bounded, fully received response. Streaming encoders
            # cannot seek back to finalize RIFF/data sizes and use sentinel values.
            pcm = audio.readframes(min(frames, MAX_AUDIO_BYTES // 2 + 1))
            streaming_size = _streaming_pcm_size(data)
            if streaming_size is not None:
                if streaming_size != len(pcm) or streaming_size % 2:
                    raise ValueError()
                frames = streaming_size // 2
            if not 0 < frames <= rate * MAX_AUDIO_SECONDS or len(pcm) != frames * 2:
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
    provider = os.getenv("TTS_PROVIDER", "kokoro").strip() or "kokoro"
    if provider == "kokoro":
        from glasses_kokoro import synthesize_local

        voice_stage("synthesis_local")
        return await synthesize_local(text)
    if provider != "openai_compatible":
        raise SpeechUnavailable("tts_not_configured")
    voice_stage("synthesis_remote")
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
