"""Shared GPU speech provider for dictation, completed glasses captures and replay tests."""

import io
import json
import logging
import os
import queue
import threading
import time
import wave
from urllib.parse import urlsplit

import requests

MAX_AUDIO_BYTES = 30 * 32000 + 4096
MAX_DICTATION_SECONDS = 600
MAX_DICTATION_BYTES = MAX_DICTATION_SECONDS * 32000 + 4096


class EvalFailure(Exception):
    """Only fixed reasons and bounded metadata cross the API boundary."""

    def __init__(self, reason: str, stage: str = "request", http_status=None):
        super().__init__(reason)
        self.stage = stage if stage in {"request", "inference"} else "request"
        self.http_status = (
            http_status if isinstance(http_status, int) and 100 <= http_status <= 599 else None
        )


def enabled() -> bool:
    return os.getenv("GLASSES_SPEECH_EVAL_ENABLED", "false").lower() == "true"


def validate_audio(audio: bytes, max_seconds: int = 30) -> None:
    if len(audio) > max_seconds * 32000 + 4096:
        raise EvalFailure("audio_too_large")
    try:
        with wave.open(io.BytesIO(audio)) as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (
                1,
                2,
                16000,
                "NONE",
            ):
                raise EvalFailure("invalid_audio")
            frames = wav.getnframes()
            if not 0 < frames <= max_seconds * 16000 or len(wav.readframes(frames)) != frames * 2:
                raise EvalFailure("invalid_audio")
    except (wave.Error, EOFError, OSError):
        raise EvalFailure("invalid_audio") from None


MODEL = "large-v3-turbo"
UPSTREAM_MODEL = "whisper-large-v3-turbo"
REPLAY_MODELS = {MODEL: UPSTREAM_MODEL, "parakeet-tdt-0.6b-v3": "parakeet-tdt-0.6b-v3"}
# Versioned, shared vocabulary: never derived from a sample's expected answer.
COMMAND_VOCABULARY = (
    "Hey Brain. Okay Brain. Open the front gate. Open the front door. "
    "Toggle the car gate. Open the car gate. Car gate. Take a picture. Take a pic. Take a photo."
)
logger = logging.getLogger(__name__)
_slot = threading.BoundedSemaphore(2)
# One lease per session, bounded by the same two inference slots. No shared mutable
# session state across concurrent users, and no transcription retries.
_sessions = queue.LifoQueue(maxsize=2)
for _ in range(2):
    _session = requests.Session()
    _session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0, pool_maxsize=1))
    _session.mount("http://", requests.adapters.HTTPAdapter(max_retries=0, pool_maxsize=1))
    _sessions.put(_session)


def configuration() -> tuple[str, str]:
    base = (
        (
            os.getenv("SPEECH_BASE_URL")
            or os.getenv("GLASSES_TRANSCRIPTION_BASE_URL")
            or os.getenv("GLASSES_SPEECH_EVAL_REMOTE_BASE_URL")
            or os.getenv("LLM_BASE_URL", "")
        )
        .strip()
        .rstrip("/")
    )
    key = os.getenv("SPEECH_API_KEY")
    if key is None:
        key = os.getenv("GLASSES_TRANSCRIPTION_API_KEY")
    if key is None:
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


def request(
    model: str,
    operation: str,
    audio: bytes = b"",
    threads: int | None = None,
    *,
    replay: bool = True,
    max_seconds: int = 30,
    vocabulary: str = "none",
) -> dict:
    started = time.monotonic()
    try:
        if replay and not enabled():
            raise EvalFailure("evaluation_disabled")
        selected = MODEL if model == UPSTREAM_MODEL else model
        if selected not in REPLAY_MODELS or operation not in {"prepare", "transcribe"}:
            raise EvalFailure("invalid_model")
        if not replay and selected != MODEL:
            raise EvalFailure("invalid_model")
        if threads is not None:
            raise EvalFailure("invalid_cpu_threads")
        if vocabulary not in {"none", "commands-v1"}:
            raise EvalFailure("invalid_vocabulary")
        upstream_model = REPLAY_MODELS[selected]
        if vocabulary == "commands-v1" and selected != MODEL:
            upstream_model = os.getenv("SPEECH_EVAL_PARAKEET_BOOSTED_MODEL", "").strip()
            if not upstream_model or upstream_model == REPLAY_MODELS[selected]:
                raise EvalFailure("vocabulary_not_configured")
            if len(upstream_model) > 128 or any(ord(c) < 32 for c in upstream_model):
                raise EvalFailure("vocabulary_not_configured")
        url, key = configuration()
        metadata = {
            "engine": "openai-compatible",
            "provider": "remote",
            "model": selected,
            "upstreamModel": upstream_model,
            "vocabularyProfile": vocabulary,
            "vocabularyMethod": "none"
            if vocabulary == "none"
            else "prompt"
            if selected == MODEL
            else "server_alias",
            "coldStartControlled": False,
        }
        if operation == "prepare":
            # No remote model-load/unload control. Do not claim the model is warm.
            return metadata | {"prepared": True}
        validate_audio(audio, max_seconds)
        if not _slot.acquire(timeout=1):
            raise EvalFailure("server_busy")
        session = _sessions.get_nowait()
        session.cookies.clear()
        try:
            logger.info(
                "[speech] started provider=remote model=%s operation=transcribe audio_bytes=%d",
                selected,
                len(audio),
            )
            upstream_at = time.monotonic()
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            fields = {"model": upstream_model, "response_format": "json"}
            if selected == MODEL:
                fields.update(language="en", temperature="0")
                if vocabulary == "commands-v1":
                    fields["prompt"] = COMMAND_VOCABULARY
            with session.post(
                url,
                headers=headers,
                files={"file": ("sample.wav", audio, "audio/wav")},
                data=fields,
                timeout=(5, 60 if max_seconds > 30 else 15),
                allow_redirects=False,
                stream=True,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise EvalFailure("remote_http_error", "inference", response.status_code)
                body = bytearray()
                for chunk in response.iter_content(chunk_size=4096):
                    if len(body) + len(chunk) > 32768:
                        raise EvalFailure("remote_invalid_response", "inference")
                    if time.monotonic() - upstream_at > (90 if max_seconds > 30 else 20):
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
                "[speech] completed provider=remote model=%s upstream_request_ms=%d server_total_ms=%d",
                selected,
                upstream_ms,
                total_ms,
            )
            return metadata | {
                "text": result["text"],
                "upstreamRequestMs": upstream_ms,
                "serverTotalMs": total_ms,
                "decoding": {"language": "en", "temperature": 0}
                if selected == MODEL
                else {"language": "provider_default"},
            }
        finally:
            session.cookies.clear()
            _sessions.put_nowait(session)
            _slot.release()
    except requests.Timeout:
        failure = EvalFailure("network_timeout", "inference")
    except requests.RequestException:
        failure = EvalFailure("network_unavailable", "inference")
    except EvalFailure as error:
        failure = error
    logger.warning(
        "[speech] failed provider=remote model=%s operation=%s reason=%s stage=%s upstream_http_status=%s elapsed_ms=%d",
        model if model in REPLAY_MODELS or model == UPSTREAM_MODEL else "unsupported",
        operation if operation in {"prepare", "transcribe"} else "unsupported",
        str(failure),
        failure.stage,
        failure.http_status,
        round((time.monotonic() - started) * 1000),
    )
    raise failure from None


def transcribe(audio: bytes, *, dictation: bool = False) -> dict:
    """Single GPU path for completed wake captures or user-started dictation; no retries."""
    return request(
        MODEL,
        "transcribe",
        audio,
        replay=False,
        max_seconds=MAX_DICTATION_SECONDS if dictation else 30,
        vocabulary="none" if dictation else "commands-v1",
    )
