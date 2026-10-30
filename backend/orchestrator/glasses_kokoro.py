"""Serialized CPU-only synthesis with image-bundled, checksum-verified assets."""

import asyncio
import io
import os
import threading
import wave
from concurrent.futures import ThreadPoolExecutor

from glasses_tts import SpeechUnavailable, pcm_payload
from voice_context import voice_audio

_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="glasses-kokoro")
_slot = threading.BoundedSemaphore(1)
_engine = None
_engine_key = None


def _generate(text):
    global _engine, _engine_key
    try:
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        model = os.getenv("KOKORO_MODEL_PATH", "/app/models/kokoro/model.onnx")
        voices = os.getenv("KOKORO_VOICES_PATH", "/app/models/kokoro/voices.bin")
        if not os.path.isfile(model) or not os.path.isfile(voices):
            raise SpeechUnavailable("tts_not_configured")
        if _engine is None or _engine_key != (model, voices):
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(
                model, sess_options=options, providers=["CPUExecutionProvider"]
            )
            _engine = Kokoro.from_session(session, voices)
            _engine_key = (model, voices)
        samples, rate = _engine.create(
            text, voice=os.getenv("KOKORO_VOICE", "af_heart"), speed=1.0, lang="en-us"
        )
        voice_audio(len(samples), rate)
        if len(samples) == 0:
            raise SpeechUnavailable("tts_audio_empty")
        if len(samples) > rate * 45:
            raise SpeechUnavailable("tts_audio_too_long")
        pcm = bytearray()
        for sample in samples:
            pcm.extend(
                int(max(-1.0, min(1.0, float(sample))) * 32767).to_bytes(2, "little", signed=True)
            )
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(int(rate))
            audio.writeframes(pcm)
        return pcm_payload(output.getvalue())
    except SpeechUnavailable:
        raise
    except ImportError:
        raise SpeechUnavailable("tts_not_configured") from None
    except Exception:
        raise SpeechUnavailable("tts_failed") from None
    finally:
        _slot.release()


async def synthesize_local(text):
    # A timed-out native inference retains its slot until the worker really finishes.
    # New turns fail promptly instead of building an unbounded executor queue.
    if not _slot.acquire(blocking=False):
        raise SpeechUnavailable("tts_failed")
    try:
        future = _worker.submit(_generate, text)
    except Exception:
        _slot.release()
        raise
    try:
        return await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), 25)
    except TimeoutError:
        raise SpeechUnavailable("tts_failed") from None
