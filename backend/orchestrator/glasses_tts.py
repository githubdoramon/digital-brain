"""CPU-only Kokoro speech synthesis for smart-glasses replies."""

from __future__ import annotations

import io
import os
import threading
import time
import wave
from contextvars import ContextVar, Token
from typing import Any


class TTSUnavailableError(RuntimeError):
    """Kokoro is not installed/configured or could not synthesize audio."""


_engine: Any | None = None
_engine_key: tuple[str, str] | None = None
_engine_lock = threading.Lock()
_synthesis_slots = threading.BoundedSemaphore(1)
_synthesis_limit = 1
_active_timings: ContextVar[dict[str, float] | None] = ContextVar(
    "glasses_tts_timings", default=None
)


def _record_timing(name: str, started_at: float) -> None:
    timings = _active_timings.get()
    if timings is not None:
        timings[name] = round(max(0.0, time.perf_counter() - started_at) * 1_000, 1)


def synthesize_kokoro_with_timings(
    text: str,
    timings: dict[str, float],
    synthesizer: Any | None = None,
) -> bytes:
    """Run the public synthesizer while collecting its per-stage timings."""
    token: Token[dict[str, float] | None] = _active_timings.set(timings)
    try:
        return (synthesizer or synthesize_kokoro)(text)
    finally:
        _active_timings.reset(token)


def warm_kokoro() -> dict[str, float | str]:
    """Load Kokoro and run one tiny inference during application startup.

    The process-local engine remains referenced by ``_engine`` after this call,
    so the first glasses reply avoids model initialization and ONNX's
    first-inference setup cost. Missing configuration and warmup failures are
    reported to the caller without preventing backend startup.
    """
    if not _config("KOKORO_MODEL_PATH", "") or not _config("KOKORO_VOICES_PATH", ""):
        return {"outcome": "skipped_not_configured"}

    started_at = time.perf_counter()
    timings: dict[str, float] = {}
    try:
        # Generate and discard a minimal utterance to exercise model load and
        # inference initialization. No user text or audio is persisted.
        synthesize_kokoro_with_timings("Ready.", timings)
    except Exception as exc:
        cause = exc.__cause__
        return {
            **timings,
            "startup_warmup_ms": round(
                max(0.0, time.perf_counter() - started_at) * 1_000, 1
            ),
            "outcome": "failed",
            "failure_type": type(cause or exc).__name__,
        }

    return {
        **timings,
        "startup_warmup_ms": round(
            max(0.0, time.perf_counter() - started_at) * 1_000, 1
        ),
        "outcome": "warmed",
    }


def _synthesis_semaphore() -> threading.BoundedSemaphore:
    global _synthesis_limit, _synthesis_slots
    try:
        limit = max(1, int(os.getenv("KOKORO_MAX_CONCURRENCY", "1")))
    except ValueError:
        limit = 1
    # Recreate only when configuration changes; the common default remains
    # serialized to keep CPU/RAM bounded on the orchestrator worker.
    if _synthesis_limit != limit:
        with _engine_lock:
            if _synthesis_limit != limit:
                _synthesis_slots = threading.BoundedSemaphore(limit)
                _synthesis_limit = limit
    return _synthesis_slots


def _config(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def synthesize_kokoro(text: str) -> bytes:
    """Synthesize complete mono PCM WAV using a CPU-only Kokoro ONNX runtime.

    The dependency and model are deliberately optional at import time so the
    backend can start when voice artifacts have not yet been provisioned.
    """
    total_started_at = time.perf_counter()
    timings = _active_timings.get()
    if timings is not None:
        timings["text_character_count"] = float(len(text))
        timings["text_word_count"] = float(len(text.split()))
        timings["process_id"] = float(os.getpid())
    model_path = _config("KOKORO_MODEL_PATH", "")
    voices_path = _config("KOKORO_VOICES_PATH", "")
    voice = _config("KOKORO_VOICE", "af_heart")
    lang = _config("KOKORO_LANG_CODE", "en-us")
    if not model_path or not voices_path:
        _record_timing("call_total_ms", total_started_at)
        raise TTSUnavailableError(
            "Kokoro is not configured; set KOKORO_MODEL_PATH and KOKORO_VOICES_PATH"
        )
    try:
        import_started_at = time.perf_counter()
        from kokoro_onnx import Kokoro  # type: ignore[import-not-found]
        _record_timing("import_ms", import_started_at)
    except ImportError as exc:
        _record_timing("import_ms", import_started_at)
        _record_timing("call_total_ms", total_started_at)
        raise TTSUnavailableError("Kokoro ONNX runtime is not installed") from exc

    try:
        # kokoro-onnx uses ONNX Runtime and does not require a GPU provider.
        global _engine, _engine_key
        key = (model_path, voices_path)
        lock_started_at = time.perf_counter()
        with _engine_lock:
            if timings is not None:
                timings["engine_lock_wait_ms"] = round(
                    max(0.0, time.perf_counter() - lock_started_at) * 1_000, 1
                )
            engine_load_started_at = time.perf_counter()
            cold_start = _engine is None or _engine_key != key
            if timings is not None:
                timings["cold_start"] = 1.0 if cold_start else 0.0
            try:
                if cold_start:
                    try:
                        _engine = Kokoro(
                            model_path,
                            voices_path,
                            providers=["CPUExecutionProvider"],
                        )
                    except TypeError:
                        # Older kokoro-onnx releases select CPU by default and do
                        # not expose the providers argument.
                        _engine = Kokoro(model_path, voices_path)
                    _engine_key = key
                engine = _engine
            finally:
                if timings is not None:
                    timings["engine_load_ms"] = round(
                        max(0.0, time.perf_counter() - engine_load_started_at) * 1_000, 1
                    )
            if timings is not None:
                try:
                    providers = engine.sess.get_providers()
                except Exception:
                    providers = []
                timings["execution_provider_count"] = float(len(providers))
                timings["execution_provider_accelerated"] = float(
                    any(provider != "CPUExecutionProvider" for provider in providers)
                )
        semaphore = _synthesis_semaphore()
        semaphore_wait_started_at = time.perf_counter()
        semaphore.acquire()
        if timings is not None:
            timings["semaphore_wait_ms"] = round(
                max(0.0, time.perf_counter() - semaphore_wait_started_at) * 1_000, 1
            )
        try:
            inference_started_at = time.perf_counter()
            try:
                samples, sample_rate = engine.create(text, voice=voice, speed=1.0, lang=lang)
            finally:
                _record_timing("engine_create_ms", inference_started_at)
            sample_count = len(samples)
            if timings is not None:
                timings["sample_count"] = float(sample_count)
                timings["sample_rate_hz"] = float(sample_rate)
                timings["audio_duration_ms"] = round(sample_count / int(sample_rate) * 1_000, 1)
            encode_started_at = time.perf_counter()
            try:
                wav_bytes = _mono_wav(samples, int(sample_rate))
            finally:
                _record_timing("wav_encode_ms", encode_started_at)
            if timings is not None:
                timings["wav_size_bytes"] = float(len(wav_bytes))
            return wav_bytes
        finally:
            semaphore.release()
    except Exception as exc:
        raise TTSUnavailableError(f"Kokoro synthesis failed: {exc}") from exc
    finally:
        if timings is not None:
            timings["call_total_ms"] = round(
                max(0.0, time.perf_counter() - total_started_at) * 1_000, 1
            )


def _mono_wav(samples: Any, sample_rate: int) -> bytes:
    """Encode floating-point or integer samples as 16-bit mono WAV."""
    try:
        import numpy as np
    except ImportError:
        np = None

    if np is not None:
        try:
            values = np.asarray(samples, dtype=np.float64)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TTSUnavailableError("Kokoro returned non-numeric audio samples") from exc
        if values.ndim != 1 or not np.isfinite(values).all():
            raise TTSUnavailableError("Kokoro returned invalid audio samples")
        clipped = np.clip(values, -1.0, 1.0)
        pcm = (clipped * 32767.0).astype("<i2", copy=False).tobytes()
    else:
        try:
            values = list(samples)
        except TypeError as exc:
            raise TTSUnavailableError("Kokoro returned invalid audio samples") from exc
        pcm_buffer = bytearray()
        for sample in values:
            try:
                value = float(sample)
            except (TypeError, ValueError) as exc:
                raise TTSUnavailableError("Kokoro returned non-numeric audio samples") from exc
            value = max(-1.0, min(1.0, value))
            pcm_buffer.extend(int(value * 32767).to_bytes(2, "little", signed=True))
        pcm = bytes(pcm_buffer)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()
