"""CPU-only Kokoro speech synthesis for smart-glasses replies."""

from __future__ import annotations

import importlib.metadata
import io
import os
import threading
import time
import wave
from contextvars import ContextVar, Token
from functools import cache
from pathlib import Path
from typing import Any


class TTSUnavailableError(RuntimeError):
    """Kokoro is not installed/configured or could not synthesize audio."""


_engine: Any | None = None
_engine_key: tuple[str, str] | None = None
_engine_lock = threading.Lock()
_synthesis_slots = threading.BoundedSemaphore(1)
_synthesis_limit = 1
_active_timings: ContextVar[dict[str, Any] | None] = ContextVar(
    "glasses_tts_timings", default=None
)


def _record_accumulated_timing(name: str, started_at: float, *, count: bool = False) -> None:
    timings = _active_timings.get()
    if timings is None:
        return
    elapsed_ms = max(0.0, time.perf_counter() - started_at) * 1_000
    timings[name] = round(timings.get(name, 0.0) + elapsed_ms, 1)
    if count:
        count_name = f"{name.removesuffix('_ms')}_count"
        timings[count_name] = timings.get(count_name, 0.0) + 1.0


class _TimedInferenceSession:
    """Delegate the ORT session while recording calls made by kokoro-onnx."""

    def __init__(self, session: Any):
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def run(self, *args: Any, **kwargs: Any) -> Any:
        started_at = time.perf_counter()
        try:
            return self._session.run(*args, **kwargs)
        finally:
            _record_accumulated_timing("onnx_inference_ms", started_at, count=True)


def _install_engine_timing_hooks(engine: Any) -> None:
    """Instrument the pinned kokoro-onnx call sites without changing its output."""
    session = getattr(engine, "sess", None)
    if session is not None and not isinstance(session, _TimedInferenceSession):
        engine.sess = _TimedInferenceSession(session)

    tokenizer = getattr(engine, "tokenizer", None)
    if tokenizer is not None and not getattr(tokenizer, "_digital_brain_timing_hooks", False):
        for method_name, timing_name in (
            ("phonemize", "phonemize_ms"),
            ("tokenize", "tokenize_ms"),
        ):
            original = getattr(tokenizer, method_name)

            def timed_method(*args: Any, _original: Any = original, _name: str = timing_name, **kwargs: Any) -> Any:
                started_at = time.perf_counter()
                try:
                    return _original(*args, **kwargs)
                finally:
                    _record_accumulated_timing(_name, started_at, count=True)

            setattr(tokenizer, method_name, timed_method)
        tokenizer._digital_brain_timing_hooks = True

    # kokoro-onnx 0.4.9 resolves this module global once per audio batch.
    # Measure its silence trimming separately from ONNX inference.
    create_globals = getattr(engine.create, "__globals__", {})
    original_trim = create_globals.get("trim_audio")
    if callable(original_trim) and not getattr(original_trim, "_digital_brain_timed", False):
        def timed_trim(*args: Any, **kwargs: Any) -> Any:
            started_at = time.perf_counter()
            try:
                return original_trim(*args, **kwargs)
            finally:
                _record_accumulated_timing("trim_audio_ms", started_at, count=True)

        timed_trim._digital_brain_timed = True
        create_globals["trim_audio"] = timed_trim


def _cpu_capacity_timings() -> dict[str, float]:
    """Expose the process CPU ceiling that can dominate CPU-only inference."""
    values: dict[str, float] = {}
    values["cpu_count"] = float(os.cpu_count() or 0)
    try:
        values["cpu_affinity_count"] = float(len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        values["cpu_affinity_count"] = -1.0

    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            values["cgroup_cpu_quota_cores"] = round(float(quota) / float(period), 2)
    except (OSError, ValueError, ZeroDivisionError):
        try:
            quota = float(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
            period = float(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
            if quota > 0 and period > 0:
                values["cgroup_cpu_quota_cores"] = round(quota / period, 2)
        except (OSError, ValueError, ZeroDivisionError):
            pass
    return values


def _artifact_diagnostics(path: str, prefix: str) -> dict[str, Any]:
    """Report artifact identity without logging deployment-specific paths."""
    artifact = Path(path)
    result: dict[str, Any] = {f"{prefix}_artifact_name": artifact.name}
    try:
        result[f"{prefix}_artifact_size_bytes"] = artifact.stat().st_size
    except OSError:
        result[f"{prefix}_artifact_size_bytes"] = None
    return result


@cache
def _package_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "not_installed"
    except Exception:
        return "unavailable"


def _record_timing(name: str, started_at: float) -> None:
    timings = _active_timings.get()
    if timings is not None:
        timings[name] = round(max(0.0, time.perf_counter() - started_at) * 1_000, 1)


def synthesize_kokoro_with_timings(
    text: str,
    timings: dict[str, Any],
    synthesizer: Any | None = None,
) -> bytes:
    """Run the public synthesizer while collecting its per-stage timings."""
    token: Token[dict[str, Any] | None] = _active_timings.set(timings)
    try:
        return (synthesizer or synthesize_kokoro)(text)
    finally:
        _active_timings.reset(token)


def warm_kokoro() -> dict[str, Any]:
    """Load Kokoro and run one tiny inference during application startup.

    The process-local engine remains referenced by ``_engine`` after this call,
    so the first glasses reply avoids model initialization and ONNX's
    first-inference setup cost. Missing configuration and warmup failures are
    reported to the caller without preventing backend startup.
    """
    if not _config("KOKORO_MODEL_PATH", "") or not _config("KOKORO_VOICES_PATH", ""):
        return {"outcome": "skipped_not_configured"}

    started_at = time.perf_counter()
    timings: dict[str, Any] = {}
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
    model_path = _config("KOKORO_MODEL_PATH", "")
    voices_path = _config("KOKORO_VOICES_PATH", "")
    voice = _config("KOKORO_VOICE", "af_heart")
    lang = _config("KOKORO_LANG_CODE", "en-us")
    if timings is not None:
        timings["text_character_count"] = float(len(text))
        timings["text_word_count"] = float(len(text.split()))
        timings["process_id"] = float(os.getpid())
        timings.update(_cpu_capacity_timings())
        timings.update(_artifact_diagnostics(model_path, "model"))
        timings.update(_artifact_diagnostics(voices_path, "voices"))
        timings["kokoro_onnx_version"] = _package_version("kokoro-onnx")
        timings["onnxruntime_package_version"] = _package_version("onnxruntime")
        timings["kokoro_voice"] = voice
        timings["kokoro_language"] = lang
        timings["kokoro_max_concurrency"] = os.getenv("KOKORO_MAX_CONCURRENCY", "1")
        for variable in ("OMP_NUM_THREADS", "ORT_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
            timings[f"{variable.lower()}_env"] = os.getenv(variable, "library_default")
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
                timings["execution_providers"] = ",".join(providers) or "unavailable"
                timings["execution_provider_accelerated"] = float(
                    any(provider != "CPUExecutionProvider" for provider in providers)
                )
                try:
                    import onnxruntime as ort

                    timings["onnxruntime_available_providers"] = ",".join(
                        ort.get_available_providers()
                    )
                except Exception:
                    timings["onnxruntime_available_providers"] = "unavailable"
            _install_engine_timing_hooks(engine)
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
                if timings is not None:
                    instrumented_ms = sum(
                        timings.get(name, 0.0)
                        for name in (
                            "phonemize_ms",
                            "tokenize_ms",
                            "onnx_inference_ms",
                            "trim_audio_ms",
                        )
                    )
                    timings["engine_create_other_ms"] = round(
                        max(0.0, timings.get("engine_create_ms", 0.0) - instrumented_ms),
                        1,
                    )
            sample_count = len(samples)
            if timings is not None:
                timings["sample_count"] = float(sample_count)
                timings["sample_rate_hz"] = float(sample_rate)
                timings["audio_duration_ms"] = round(sample_count / int(sample_rate) * 1_000, 1)
                if timings["audio_duration_ms"] > 0:
                    timings["tts_real_time_factor"] = round(
                        timings.get("engine_create_ms", 0.0) / timings["audio_duration_ms"],
                        3,
                    )
            encode_started_at = time.perf_counter()
            try:
                wav_bytes = _mono_wav(samples, int(sample_rate), timings=timings)
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


def _mono_wav(
    samples: Any,
    sample_rate: int,
    *,
    timings: dict[str, Any] | None = None,
) -> bytes:
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
        if timings is not None:
            analysis_started_at = time.perf_counter()
            absolute = np.abs(clipped)
            timings["audio_peak"] = float(np.max(absolute)) if absolute.size else 0.0
            timings["audio_rms"] = (
                float(np.sqrt(np.mean(np.square(clipped)))) if clipped.size else 0.0
            )
            timings["audio_near_silence_fraction"] = (
                float(np.count_nonzero(absolute < 0.0001) / absolute.size)
                if absolute.size
                else 1.0
            )
            timings["audio_signal_analysis_ms"] = round(
                max(0.0, time.perf_counter() - analysis_started_at) * 1_000, 1
            )
        pcm = (clipped * 32767.0).astype("<i2", copy=False).tobytes()
    else:
        try:
            values = list(samples)
        except TypeError as exc:
            raise TTSUnavailableError("Kokoro returned invalid audio samples") from exc
        pcm_buffer = bytearray()
        peak = 0.0
        square_sum = 0.0
        near_silence_count = 0
        analysis_started_at = time.perf_counter()
        for sample in values:
            try:
                value = float(sample)
            except (TypeError, ValueError) as exc:
                raise TTSUnavailableError("Kokoro returned non-numeric audio samples") from exc
            value = max(-1.0, min(1.0, value))
            peak = max(peak, abs(value))
            square_sum += value * value
            near_silence_count += int(abs(value) < 0.0001)
            pcm_buffer.extend(int(value * 32767).to_bytes(2, "little", signed=True))
        pcm = bytes(pcm_buffer)
        if timings is not None:
            sample_count = len(values)
            timings["audio_peak"] = peak
            timings["audio_rms"] = (square_sum / sample_count) ** 0.5 if sample_count else 0.0
            timings["audio_near_silence_fraction"] = (
                near_silence_count / sample_count if sample_count else 1.0
            )
            timings["audio_signal_analysis_ms"] = round(
                max(0.0, time.perf_counter() - analysis_started_at) * 1_000, 1
            )
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()
