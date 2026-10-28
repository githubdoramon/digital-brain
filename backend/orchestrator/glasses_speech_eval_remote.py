"""Opt-in replay through the configured OpenAI-compatible speech provider."""

import json
import logging
import os
import threading
import time
from urllib.parse import urlsplit

import requests

from glasses_speech_eval import EvalFailure, enabled, validate_audio

MODEL = "large-v3-turbo"
logger = logging.getLogger(__name__)
_slot = threading.Lock()


def configuration() -> tuple[str, str]:
    base = (
        (os.getenv("GLASSES_SPEECH_EVAL_REMOTE_BASE_URL") or os.getenv("LLM_BASE_URL", ""))
        .strip()
        .rstrip("/")
    )
    key = os.getenv("GLASSES_SPEECH_EVAL_REMOTE_API_KEY")
    if key is None:
        key = os.getenv("LLM_API_KEY", "")
    try:
        parsed = urlsplit(base)
    except ValueError:
        raise EvalFailure("remote_not_configured") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise EvalFailure("remote_not_configured")
    # Preserve configured gateway base paths, just as the LLM client does.
    if parsed.path in {"", "/"}:
        base += "/v1"
    return base + "/audio/transcriptions", key.strip()


def request(model: str, operation: str, audio: bytes = b"", threads: int | None = None) -> dict:
    started = time.monotonic()
    try:
        if not enabled():
            raise EvalFailure("evaluation_disabled")
        if model != MODEL or operation not in {"prepare", "transcribe"}:
            raise EvalFailure("invalid_model")
        if threads is not None:
            raise EvalFailure("invalid_cpu_threads")
        url, key = configuration()
        metadata = {
            "engine": "openai-compatible",
            "provider": "remote",
            "model": MODEL,
            "coldStartControlled": False,
        }
        if operation == "prepare":
            # No remote model-load/unload control. Do not claim the model is warm.
            return metadata | {"prepared": True}
        validate_audio(audio)
        if not _slot.acquire(timeout=1):
            raise EvalFailure("server_busy")
        try:
            logger.info(
                "[speech_eval] started provider=remote model=%s operation=transcribe audio_bytes=%d",
                MODEL,
                len(audio),
            )
            upstream_at = time.monotonic()
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            with requests.post(
                url,
                headers=headers,
                files={"file": ("sample.wav", audio, "audio/wav")},
                data={
                    "model": MODEL,
                    "language": "en",
                    "response_format": "json",
                    "temperature": "0",
                },
                timeout=(10, 60),
                allow_redirects=False,
                stream=True,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise EvalFailure("remote_http_error", "inference", response.status_code)
                body = bytearray()
                for chunk in response.iter_content(chunk_size=4096):
                    if len(body) + len(chunk) > 32768:
                        raise EvalFailure("remote_invalid_response", "inference")
                    if time.monotonic() - upstream_at > 120:
                        raise EvalFailure("server_timeout", "inference")
                    body.extend(chunk)
                try:
                    result = json.loads(body)
                except (ValueError, UnicodeError):
                    raise EvalFailure("remote_invalid_response", "inference") from None
                if not isinstance(result, dict) or not isinstance(result.get("text"), str):
                    raise EvalFailure("remote_invalid_response", "inference")
            upstream_ms = round((time.monotonic() - upstream_at) * 1000)
            total_ms = round((time.monotonic() - started) * 1000)
            logger.info(
                "[speech_eval] completed provider=remote model=%s upstream_request_ms=%d server_total_ms=%d",
                MODEL,
                upstream_ms,
                total_ms,
            )
            return metadata | {
                "text": result["text"],
                "upstreamRequestMs": upstream_ms,
                "serverTotalMs": total_ms,
                "decoding": {"language": "en", "temperature": 0},
            }
        finally:
            _slot.release()
    except requests.Timeout:
        failure = EvalFailure("network_timeout", "inference")
    except requests.RequestException:
        failure = EvalFailure("network_unavailable", "inference")
    except EvalFailure as error:
        failure = error
    logger.warning(
        "[speech_eval] failed provider=remote model=%s operation=%s reason=%s stage=%s upstream_http_status=%s elapsed_ms=%d",
        model if model == MODEL else "unsupported",
        operation if operation in {"prepare", "transcribe"} else "unsupported",
        str(failure),
        failure.stage,
        failure.http_status,
        round((time.monotonic() - started) * 1000),
    )
    raise failure from None
