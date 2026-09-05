"""CPU-only Kokoro speech synthesis for smart-glasses replies."""

from __future__ import annotations

import io
import os
import threading
import wave
from typing import Any


class TTSUnavailableError(RuntimeError):
    """Kokoro is not installed/configured or could not synthesize audio."""


_engine: Any | None = None
_engine_key: tuple[str, str] | None = None
_engine_lock = threading.Lock()
_synthesis_slots = threading.BoundedSemaphore(1)
_synthesis_limit = 1


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
    model_path = _config("KOKORO_MODEL_PATH", "")
    voices_path = _config("KOKORO_VOICES_PATH", "")
    voice = _config("KOKORO_VOICE", "af_heart")
    lang = _config("KOKORO_LANG_CODE", "en-us")
    if not model_path or not voices_path:
        raise TTSUnavailableError(
            "Kokoro is not configured; set KOKORO_MODEL_PATH and KOKORO_VOICES_PATH"
        )
    try:
        from kokoro_onnx import Kokoro  # type: ignore[import-not-found]
    except ImportError as exc:
        raise TTSUnavailableError("Kokoro ONNX runtime is not installed") from exc

    try:
        # kokoro-onnx uses ONNX Runtime and does not require a GPU provider.
        global _engine, _engine_key
        key = (model_path, voices_path)
        with _engine_lock:
            if _engine is None or _engine_key != key:
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
        with _synthesis_semaphore():
            samples, sample_rate = engine.create(text, voice=voice, speed=1.0, lang=lang)
            return _mono_wav(samples, int(sample_rate))
    except Exception as exc:
        raise TTSUnavailableError(f"Kokoro synthesis failed: {exc}") from exc


def _mono_wav(samples: Any, sample_rate: int) -> bytes:
    """Encode floating-point or integer samples as 16-bit mono WAV."""
    try:
        values = list(samples)
    except TypeError as exc:
        raise TTSUnavailableError("Kokoro returned invalid audio samples") from exc
    pcm = bytearray()
    for sample in values:
        try:
            value = float(sample)
        except (TypeError, ValueError) as exc:
            raise TTSUnavailableError("Kokoro returned non-numeric audio samples") from exc
        value = max(-1.0, min(1.0, value))
        pcm.extend(int(value * 32767).to_bytes(2, "little", signed=True))
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(pcm))
    return output.getvalue()
