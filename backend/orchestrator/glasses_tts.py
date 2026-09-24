"""OpenAI-compatible remote TTS for smart-glasses replies."""

from __future__ import annotations

import math
import os
import threading
import time
import wave
from io import BytesIO
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)


class TTSUnavailableError(RuntimeError):
    """Remote speech synthesis is unconfigured or failed."""


_http_client: httpx.Client | None = None
_http_client_lock = threading.Lock()


def _config(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _speech_endpoint() -> str:
    """Build the speech endpoint from a host, API base, or full endpoint URL."""
    configured = _config("TTS_BASE_URL")
    if not configured:
        raise TTSUnavailableError("Qwen TTS is not configured; set TTS_BASE_URL")

    parsed = urlsplit(configured)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise TTSUnavailableError("TTS_BASE_URL must be an HTTP(S) base URL")

    path = parsed.path.rstrip("/")
    if path.endswith("/v1/audio/speech"):
        pass
    elif path.endswith("/v1"):
        path = f"{path}/audio/speech"
    else:
        path = f"{path}/v1/audio/speech"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _get_http_client() -> httpx.Client:
    """Share a thread-safe connection pool across command worker threads."""
    global _http_client
    if _http_client is None:
        with _http_client_lock:
            if _http_client is None:
                _http_client = httpx.Client(
                    headers={"Accept": "audio/wav"},
                    follow_redirects=False,
                )
    return _http_client


def close_tts_http_client() -> None:
    """Close pooled TTS connections during application shutdown."""
    global _http_client
    with _http_client_lock:
        if _http_client is not None:
            _http_client.close()
            _http_client = None


def get_tts_provider_status() -> dict[str, Any]:
    """Return safe startup diagnostics without contacting the provider."""
    configured = bool(_config("TTS_BASE_URL"))
    if not configured:
        outcome = "not_configured"
    else:
        try:
            _speech_endpoint()
            outcome = "configured"
        except TTSUnavailableError:
            outcome = "invalid_configuration"
    return {
        "provider": "openai_compatible_qwen_tts",
        "outcome": outcome,
        "model": _config("TTS_MODEL", "qwen3-tts"),
        "voice": _config("TTS_VOICE", "aiden"),
        "authorization_configured": bool(_config("TTS_API_KEY")),
    }


def _request_timeout(timeout_seconds: float | None) -> httpx.Timeout:
    try:
        configured = float(os.getenv("TTS_TIMEOUT_SECONDS", "45"))
    except ValueError:
        configured = 45.0
    if not math.isfinite(configured) or configured <= 0:
        configured = 45.0

    effective = configured
    if timeout_seconds is not None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise TTSUnavailableError("No time remains for Qwen TTS")
        effective = min(effective, timeout_seconds)
    effective = max(0.1, effective)
    return httpx.Timeout(effective, connect=min(5.0, effective))


def _validate_wav(data: bytes) -> tuple[int, int, int, int]:
    """Validate nonempty uncompressed PCM WAV suitable for mobile playback."""
    try:
        with wave.open(BytesIO(data), "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frame_count = audio.getnframes()
            if (
                audio.getcomptype() != "NONE"
                or channels not in {1, 2}
                or sample_width not in {1, 2, 3, 4}
                or sample_rate <= 0
                or frame_count <= 0
            ):
                raise TTSUnavailableError("Qwen TTS returned unsupported WAV audio")
            pcm = audio.readframes(frame_count)
            if len(pcm) != frame_count * channels * sample_width:
                raise TTSUnavailableError("Qwen TTS returned incomplete WAV audio")
            return channels, sample_width, sample_rate, frame_count
    except TTSUnavailableError:
        raise
    except (EOFError, OSError, ValueError, wave.Error) as exc:
        raise TTSUnavailableError("Qwen TTS returned invalid WAV audio") from exc


def synthesize_speech(
    text: str,
    *,
    command_id: str | None = None,
    timeout_seconds: float | None = None,
    timings: dict[str, Any] | None = None,
) -> bytes:
    """Request a complete WAV from the configured OpenAI-compatible TTS API.

    The endpoint and bearer token are deliberately independent of LLM settings.
    Neither user text, credentials, nor the configured endpoint are logged.
    """
    started_at = time.perf_counter()
    metrics = timings if timings is not None else {}
    model = _config("TTS_MODEL", "qwen3-tts")
    voice = _config("TTS_VOICE", "aiden")
    status_code: int | None = None
    response_bytes = 0
    outcome = "failed"
    failure_type: str | None = None
    sample_rate = 0
    frame_count = 0

    metrics.update(
        {
            "provider": "openai_compatible_qwen_tts",
            "model": model,
            "voice": voice,
            "text_character_count": len(text),
            "text_word_count": len(text.split()),
            "command_id_forwarded": bool(command_id),
        }
    )

    try:
        endpoint = _speech_endpoint()
        headers: dict[str, str] = {}
        api_key = _config("TTS_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if command_id:
            headers["X-Glasses-Command-Id"] = command_id
            headers["X-Request-ID"] = command_id

        request_started_at = time.perf_counter()
        try:
            with _get_http_client().stream(
                "POST",
                endpoint,
                headers=headers,
                json={
                    "model": model,
                    "voice": voice,
                    "input": text,
                    "response_format": "wav",
                },
                timeout=_request_timeout(timeout_seconds),
            ) as response:
                status_code = response.status_code
                metrics["http_status_code"] = status_code
                metrics["response_headers_ms"] = round(
                    max(0.0, time.perf_counter() - request_started_at) * 1_000, 1
                )
                if not 200 <= status_code < 300:
                    raise TTSUnavailableError(f"Qwen TTS returned HTTP {status_code}")

                body_started_at = time.perf_counter()
                audio_bytes = b"".join(response.iter_bytes())
                metrics["response_body_ms"] = round(
                    max(0.0, time.perf_counter() - body_started_at) * 1_000, 1
                )
                response_bytes = len(audio_bytes)
        except TTSUnavailableError:
            raise
        except httpx.RequestError as exc:
            failure_type = type(exc).__name__
            raise TTSUnavailableError(
                f"Qwen TTS request failed ({failure_type})"
            ) from None
        finally:
            metrics["request_total_ms"] = round(
                max(0.0, time.perf_counter() - request_started_at) * 1_000, 1
            )

        metrics["response_bytes"] = response_bytes
        wav_started_at = time.perf_counter()
        channels, sample_width, sample_rate, frame_count = _validate_wav(audio_bytes)
        metrics["wav_validation_ms"] = round(
            max(0.0, time.perf_counter() - wav_started_at) * 1_000, 1
        )
        duration_ms = round(frame_count / sample_rate * 1_000, 1)
        metrics.update(
            {
                "audio_channels": channels,
                "audio_sample_width_bytes": sample_width,
                "audio_sample_rate_hz": sample_rate,
                "audio_frame_count": frame_count,
                "audio_duration_ms": duration_ms,
            }
        )
        outcome = "success"
        return audio_bytes
    except TTSUnavailableError:
        failure_type = type(exc).__name__
        raise
    except Exception as exc:
        failure_type = type(exc).__name__
        raise TTSUnavailableError(f"Qwen TTS request failed ({failure_type})") from None
    finally:
        total_ms = round(max(0.0, time.perf_counter() - started_at) * 1_000, 1)
        metrics["call_total_ms"] = total_ms
        metrics["outcome"] = outcome
        if failure_type:
            metrics["failure_type"] = failure_type
        metrics.setdefault("response_bytes", response_bytes)
        if outcome == "success":
            logger.info(
                "[glasses] TTS provider request command_id=%s provider=qwen_openai "
                "model=%s voice=%s outcome=success status=%s request_ms=%s "
                "headers_ms=%s body_ms=%s response_bytes=%s audio_duration_ms=%s "
                "sample_rate_hz=%s",
                command_id or "unavailable",
                model,
                voice,
                status_code,
                metrics.get("request_total_ms"),
                metrics.get("response_headers_ms"),
                metrics.get("response_body_ms"),
                response_bytes,
                metrics.get("audio_duration_ms"),
                sample_rate,
            )
        else:
            logger.warning(
                "[glasses] TTS provider request command_id=%s provider=qwen_openai "
                "model=%s voice=%s outcome=failed status=%s failure_type=%s "
                "request_ms=%s response_bytes=%s",
                command_id or "unavailable",
                model,
                voice,
                status_code,
                failure_type or "unavailable",
                metrics.get("request_total_ms"),
                response_bytes,
            )


def synthesize_speech_with_timings(
    text: str,
    timings: dict[str, Any],
    command_id: str | None = None,
    timeout_seconds: float | None = None,
    synthesizer: Any | None = None,
) -> bytes:
    """Keep the provider call and all HTTP/WAV timings in one mutable record."""
    return (synthesizer or synthesize_speech)(
        text,
        command_id=command_id,
        timeout_seconds=timeout_seconds,
        timings=timings,
    )
