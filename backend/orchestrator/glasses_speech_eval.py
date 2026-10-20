"""CPU-only replay inference. Audio exists only in bounded IPC/memory; no action dispatch."""

from __future__ import annotations

import atexit
import errno
import io
import logging
import multiprocessing as mp
import os
import threading
import time
import wave
from contextlib import suppress
from enum import Enum
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


logger = logging.getLogger(__name__)


class EvalStage(str, Enum):
    REQUEST = "request"
    INITIALIZATION = "initialization"
    DOWNLOAD = "model_download"
    LOAD = "model_load"
    INFERENCE = "inference"


class EvalFailure(Exception):
    """Only fixed reasons and allow-listed metadata cross the API boundary."""

    def __init__(self, reason: str, stage: str = EvalStage.REQUEST.value, http_status=None):
        super().__init__(reason)
        self.stage = (
            stage if stage in {item.value for item in EvalStage} else EvalStage.REQUEST.value
        )
        self.http_status = (
            http_status if isinstance(http_status, int) and 100 <= http_status <= 599 else None
        )


def classify_failure(error: Exception, stage: EvalStage) -> dict:
    """Inspect bounded exception causes, never their messages, paths, URLs or headers."""
    seen = set()
    cause = error
    network_failure = None
    for _ in range(4):
        if cause is None or id(cause) in seen:
            break
        seen.add(id(cause))
        status = getattr(getattr(cause, "response", None), "status_code", None)
        if isinstance(status, int) and 100 <= status <= 599:
            return {
                "error": "model_download_http_error"
                if stage == EvalStage.DOWNLOAD
                else "engine_http_error",
                "stage": stage.value,
                "upstreamHttpStatus": status,
            }
        if isinstance(cause, MemoryError) or getattr(cause, "errno", None) == errno.ENOMEM:
            return {"error": "insufficient_memory", "stage": stage.value}
        if isinstance(cause, OSError):
            reason = {
                errno.ENOSPC: "cache_storage_full",
                errno.EACCES: "cache_access_denied",
                errno.EROFS: "cache_access_denied",
            }.get(cause.errno)
            if reason:
                return {"error": reason, "stage": stage.value}
        names = {base.__name__ for base in type(cause).__mro__}
        if isinstance(cause, TimeoutError) or names.intersection(
            {"Timeout", "TimeoutException", "ReadTimeout", "ConnectTimeout"}
        ):
            return {"error": "network_timeout", "stage": stage.value}
        if names.intersection(
            {"ConnectionError", "ConnectError", "NetworkError", "LocalEntryNotFoundError"}
        ):
            network_failure = "model_download_unavailable"
        cause = cause.__cause__ or cause.__context__
    if network_failure is not None:
        return {"error": network_failure, "stage": stage.value}
    return {
        "error": {
            EvalStage.INITIALIZATION: "engine_import_failed",
            EvalStage.DOWNLOAD: "model_download_failed",
            EvalStage.LOAD: "model_load_failed",
            EvalStage.INFERENCE: "inference_failed",
        }.get(stage, "engine_unavailable"),
        "stage": stage.value,
    }


def cpu_environment() -> dict:
    """Numeric process/container capacity only; no host or network identifiers."""
    result = {"logicalCpus": os.cpu_count()}
    if hasattr(os, "sched_getaffinity"):
        with suppress(OSError):
            result["affinityCpus"] = len(os.sched_getaffinity(0))
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            result["quotaCpus"] = round(int(quota) / int(period), 3)
    except (OSError, ValueError, ZeroDivisionError):
        pass
    return result


def log_rejected(key: str, operation: str, reason: str):
    logger.warning(
        "[speech_eval] rejected model=%s operation=%s reason=%s",
        key if key in MODELS else "unsupported",
        operation,
        reason,
    )


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

    # Third-party exceptions may contain private paths/URLs. Only sanitized IPC reaches the parent logger.
    logging.disable(logging.CRITICAL)
    try:
        import numpy as np
        from faster_whisper import WhisperModel
        from huggingface_hub import snapshot_download
    except Exception as error:
        pipe.send(classify_failure(error, EvalStage.INITIALIZATION))
        return
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
        stage = EvalStage.DOWNLOAD
        pipe.send({"event": "stage", "stage": stage.value})
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
                    {
                        "prepared": True,
                        "modelRevision": revision,
                        "modelDownloadMs": download_ms,
                        "cpuThreads": threads,
                    }
                )
                continue
            already_loaded = model is not None and current == key and not cold
            load_start = time.monotonic()
            stage = EvalStage.LOAD
            pipe.send({"event": "stage", "stage": stage.value})
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
            stage = EvalStage.INFERENCE
            pipe.send({"event": "stage", "stage": stage.value})
            inference_start = time.monotonic()
            cpu_start = time.process_time()
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
                    "inferenceCpuMs": round((time.process_time() - cpu_start) * 1000),
                    "engine": "faster-whisper",
                    "engineVersion": version("faster-whisper"),
                    "computeType": "int8",
                    "cpuThreads": threads,
                    "decoding": {"language": "en", "beamSize": 1, "temperature": 0, "vad": False},
                }
            )
        except Exception as error:
            pipe.send(classify_failure(error, stage))
            # Reset native state after a failed inference/load rather than trusting a partial context.
            model = None
            current = None
        finally:
            # Keep the model cache, never the preceding utterance, while waiting for another job.
            message = audio = samples = text = segments = None


class SpeechEvalEngine:
    def __init__(self):
        self.lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.active = None
        self.process = None
        self.pipe = None
        self.threads = None

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
        self.threads = None

    def request(
        self,
        key: str,
        operation: str,
        cold: bool = False,
        audio: bytes = b"",
        threads: int | None = None,
    ) -> dict:
        if not enabled():
            log_rejected(
                key,
                operation if operation in {"prepare", "transcribe"} else "unsupported",
                "evaluation_disabled",
            )
            raise EvalFailure("evaluation_disabled")
        if key not in MODELS or operation not in {"prepare", "transcribe"}:
            log_rejected(
                key,
                operation if operation in {"prepare", "transcribe"} else "unsupported",
                "invalid_model",
            )
            raise EvalFailure("invalid_model")
        if threads is not None and threads not in {2, 4, 8}:
            raise EvalFailure("invalid_cpu_threads")
        requested_threads = (
            threads
            if threads is not None
            else max(1, min(8, int(os.getenv("GLASSES_SPEECH_EVAL_THREADS", "2"))))
        )
        if operation == "transcribe":
            validate_audio(audio)
        queued = time.monotonic()
        if not self.lock.acquire(timeout=1):
            with self.state_lock:
                active = dict(self.active) if self.active is not None else {}
            logger.warning(
                "[speech_eval] rejected model=%s operation=%s reason=server_busy active_model=%s active_operation=%s active_stage=%s active_threads=%s active_elapsed_ms=%s",
                key,
                operation,
                active.get("model"),
                active.get("operation"),
                active.get("stage"),
                active.get("threads"),
                round((time.monotonic() - active["started"]) * 1000) if active else None,
            )
            raise EvalFailure("server_busy")
        stage = EvalStage.INITIALIZATION.value
        with self.state_lock:
            self.active = {
                "model": key,
                "operation": operation,
                "stage": stage,
                "threads": requested_threads,
                "started": time.monotonic(),
            }
        logger.info(
            "[speech_eval] started model=%s operation=%s cold=%s audio_bytes=%d",
            key,
            operation,
            cold,
            len(audio),
        )
        try:
            if (
                self.process is None
                or not self.process.is_alive()
                or self.threads != requested_threads
            ):
                self.close()
                context = mp.get_context("spawn")
                self.pipe, child = context.Pipe()
                cache = str(
                    Path(os.getenv("GLASSES_SPEECH_EVAL_CACHE", "storage/whisper-models")).resolve()
                )
                threads = requested_threads
                self.process = context.Process(
                    target=_engine, args=(child, cache, threads), daemon=True
                )
                self.process.start()
                self.threads = requested_threads
                child.close()
                capacity = cpu_environment()
                logger.info(
                    "[speech_eval] engine_started cpu_threads=%d logical_cpus=%s affinity_cpus=%s quota_cpus=%s",
                    threads,
                    capacity.get("logicalCpus"),
                    capacity.get("affinityCpus"),
                    capacity.get("quotaCpus"),
                )
            queue_ms = round((time.monotonic() - queued) * 1000)
            self.pipe.send((key, operation, cold, audio))
            deadline = time.monotonic() + (600 if operation == "prepare" else 180)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self.pipe.poll(remaining):
                    self.close()
                    raise EvalFailure("server_timeout", stage)
                result = self.pipe.recv()
                if result.get("event") == "stage":
                    next_stage = result.get("stage")
                    if next_stage in {item.value for item in EvalStage}:
                        stage = next_stage
                        with self.state_lock:
                            self.active["stage"] = stage
                        logger.info(
                            "[speech_eval] stage model=%s operation=%s stage=%s elapsed_ms=%d",
                            key,
                            operation,
                            stage,
                            round((time.monotonic() - queued) * 1000),
                        )
                    continue
                if "error" in result:
                    raise EvalFailure(
                        result["error"],
                        result.get("stage", stage),
                        result.get("upstreamHttpStatus"),
                    )
                if result.get("cpuThreads") != requested_threads:
                    raise EvalFailure("cpu_configuration_mismatch", stage)
                logger.info(
                    "[speech_eval] completed model=%s operation=%s elapsed_ms=%d queue_ms=%d download_ms=%s load_ms=%s inference_ms=%s inference_cpu_ms=%s model_loaded=%s",
                    key,
                    operation,
                    round((time.monotonic() - queued) * 1000),
                    queue_ms,
                    result.get("modelDownloadMs"),
                    result.get("modelLoadMs"),
                    result.get("inferenceMs"),
                    result.get("inferenceCpuMs"),
                    result.get("modelAlreadyLoaded"),
                )
                return result | {"queueWaitMs": queue_ms}
        except EvalFailure as error:
            logger.warning(
                "[speech_eval] failed model=%s operation=%s stage=%s reason=%s upstream_http_status=%s elapsed_ms=%d",
                key,
                operation,
                error.stage,
                str(error),
                error.http_status,
                round((time.monotonic() - queued) * 1000),
            )
            raise
        except (EOFError, BrokenPipeError, OSError):
            exit_code = self.process.exitcode if self.process is not None else None
            logger.warning(
                "[speech_eval] failed model=%s operation=%s stage=%s reason=engine_unavailable child_exit_code=%s elapsed_ms=%d",
                key,
                operation,
                stage,
                exit_code,
                round((time.monotonic() - queued) * 1000),
            )
            self.close()
            raise EvalFailure("engine_unavailable", stage) from None
        finally:
            with self.state_lock:
                self.active = None
            self.lock.release()


engine = SpeechEvalEngine()
atexit.register(engine.close)
