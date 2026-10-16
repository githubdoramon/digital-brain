"""CPU-only replay inference. Audio exists only in bounded IPC/memory; no action dispatch."""

from __future__ import annotations

import atexit
import io
import multiprocessing as mp
import os
import threading
import time
import wave
from pathlib import Path

MAX_AUDIO_BYTES = 30 * 32000 + 4096
MODELS = {
    "base.en": ("Systran/faster-whisper-base.en", "3d3d5dee26484f91867d81cb899cfcf72b96be6c"),
    "small.en": ("Systran/faster-whisper-small.en", "d1d751a5f8271d482d14ca55d9e2deeebbae577f"),
    "medium.en": ("Systran/faster-whisper-medium.en", "a29b04bd15381511a9af671baec01072039215e3"),
    "large-v3": ("Systran/faster-whisper-large-v3", "edaa852ec7e145841d8ffdb056a99866b5f0a478"),
    "large-v3-turbo": (
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
    ),
}


class EvalFailure(Exception):
    """Only these fixed reasons cross the API boundary."""


def validate_audio(audio: bytes) -> None:
    if len(audio) > MAX_AUDIO_BYTES:
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
            if not 0 < frames <= 30 * 16000 or len(wav.readframes(frames)) != frames * 2:
                raise EvalFailure("invalid_audio")
    except (wave.Error, EOFError, OSError):
        raise EvalFailure("invalid_audio") from None


def enabled() -> bool:
    return os.getenv("GLASSES_SPEECH_EVAL_ENABLED", "false").lower() == "true"


def _engine(pipe, cache: str, threads: int) -> None:
    # Imports/model objects stay in a disposable process, with no request tracing/logging.
    import gc
    import logging
    from importlib.metadata import version

    import numpy as np
    from faster_whisper import WhisperModel
    from huggingface_hub import snapshot_download

    logging.disable(logging.CRITICAL)
    model = None
    current = None
    while True:
        if not pipe.poll(60):
            model = None
            current = None
            gc.collect()
            continue
        message = pipe.recv()
        key, operation, cold, audio = message
        repo, revision = MODELS[key]
        started = time.monotonic()
        try:
            path = snapshot_download(
                repo,
                revision=revision,
                cache_dir=cache,
                allow_patterns=[
                    "model.bin",
                    "config.json",
                    "vocabulary.*",
                    "tokenizer.json",
                    "preprocessor_config.json",
                ],
            )
            download_ms = round((time.monotonic() - started) * 1000)
            if operation == "prepare":
                pipe.send(
                    {"prepared": True, "modelRevision": revision, "modelDownloadMs": download_ms}
                )
                continue
            already_loaded = model is not None and current == key and not cold
            load_start = time.monotonic()
            if not already_loaded:
                model = None
                gc.collect()
                model = WhisperModel(
                    path, device="cpu", compute_type="int8", cpu_threads=threads, num_workers=1
                )
                current = key
            load_ms = round((time.monotonic() - load_start) * 1000)
            with wave.open(io.BytesIO(audio)) as wav:
                samples = (
                    np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).astype(
                        np.float32
                    )
                    / 32768.0
                )
            inference_start = time.monotonic()
            segments, _ = model.transcribe(
                samples,
                language="en",
                beam_size=1,
                best_of=1,
                temperature=0,
                condition_on_previous_text=False,
                vad_filter=False,
                without_timestamps=True,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
            pipe.send(
                {
                    "text": text,
                    "modelRevision": revision,
                    "modelAlreadyLoaded": already_loaded,
                    "modelDownloadMs": download_ms,
                    "modelLoadMs": load_ms,
                    "inferenceMs": round((time.monotonic() - inference_start) * 1000),
                    "processingMs": round((time.monotonic() - started) * 1000),
                    "engine": "faster-whisper",
                    "engineVersion": version("faster-whisper"),
                    "computeType": "int8",
                    "cpuThreads": threads,
                    "decoding": {"language": "en", "beamSize": 1, "temperature": 0, "vad": False},
                }
            )
        except Exception:
            pipe.send({"error": "inference_failed"})
            # Reset native state after a failed inference/load rather than trusting a partial context.
            model = None
            current = None
        finally:
            # Keep the model cache, never the preceding utterance, while waiting for another job.
            message = audio = samples = text = segments = None


class SpeechEvalEngine:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.pipe = None

    def close(self):
        if self.process is not None:
            self.process.terminate()
            self.process.join(timeout=3)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=3)
        if self.pipe is not None:
            self.pipe.close()
        self.process = self.pipe = None

    def request(self, key: str, operation: str, cold: bool = False, audio: bytes = b"") -> dict:
        if not enabled():
            raise EvalFailure("evaluation_disabled")
        if key not in MODELS or operation not in {"prepare", "transcribe"}:
            raise EvalFailure("invalid_model")
        if operation == "transcribe":
            validate_audio(audio)
        queued = time.monotonic()
        if not self.lock.acquire(timeout=1):
            raise EvalFailure("server_busy")
        try:
            if self.process is None or not self.process.is_alive():
                self.close()
                context = mp.get_context("spawn")
                self.pipe, child = context.Pipe()
                cache = str(
                    Path(os.getenv("GLASSES_SPEECH_EVAL_CACHE", "storage/whisper-models")).resolve()
                )
                threads = max(1, min(8, int(os.getenv("GLASSES_SPEECH_EVAL_THREADS", "2"))))
                self.process = context.Process(
                    target=_engine, args=(child, cache, threads), daemon=True
                )
                self.process.start()
                child.close()
            queue_ms = round((time.monotonic() - queued) * 1000)
            self.pipe.send((key, operation, cold, audio))
            if not self.pipe.poll(600 if operation == "prepare" else 180):
                self.close()
                raise EvalFailure("server_timeout")
            result = self.pipe.recv()
            if "error" in result:
                raise EvalFailure(result["error"])
            return result | {"queueWaitMs": queue_ms}
        except (EOFError, BrokenPipeError, OSError):
            self.close()
            raise EvalFailure("engine_unavailable") from None
        finally:
            self.lock.release()


engine = SpeechEvalEngine()
atexit.register(engine.close)
