#!/usr/bin/env python3
"""Benchmark warm Kokoro inference with a selectable ONNX thread count.

Run inside the orchestrator image so the model, ONNX Runtime build, CPU
affinity, and cgroup limits match the deployed service. The script creates an
independent session and never changes the running FastAPI worker's settings.
It prints metrics only; the spoken benchmark text is not included in output.

Example:
    python scripts/benchmark_kokoro.py --threads 4 --iterations 3
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_TEXT = (
    "The team reviewed today's schedule, confirmed the next steps, and agreed "
    "to share a brief update tomorrow. This short answer keeps the key details "
    "clear and easy to follow, with each sentence designed for natural speech "
    "on the glasses."
)


def _orchestrator_root() -> Path:
    script_path = Path(__file__)
    if script_path.exists():
        return script_path.resolve().parents[1]
    # Also support piping this file to `docker exec ... python -` so it can be
    # run against an existing image without rebuilding or copying into it.
    return Path.cwd()


sys.path.insert(0, str(_orchestrator_root()))

from glasses_tts import (  # noqa: E402
    _active_timings,
    _cpu_capacity_timings,
    _install_engine_timing_hooks,
)


def _lower_process_priority(requested_nice: int) -> int | None:
    if requested_nice <= 0 or not hasattr(os, "getpriority"):
        return None
    try:
        current = os.getpriority(os.PRIO_PROCESS, 0)
        if current < requested_nice:
            os.setpriority(os.PRIO_PROCESS, 0, requested_nice)
        return os.getpriority(os.PRIO_PROCESS, 0)
    except OSError:
        return None


def _timed_create(
    engine: Any,
    *,
    text: str,
    voice: str,
    speed: float,
    lang: str,
) -> dict[str, Any]:
    timings: dict[str, Any] = {}
    timing_token = _active_timings.set(timings)
    started_at = time.perf_counter()
    try:
        samples, sample_rate = engine.create(
            text,
            voice=voice,
            speed=speed,
            lang=lang,
        )
    finally:
        engine_create_ms = (time.perf_counter() - started_at) * 1_000
        _active_timings.reset(timing_token)

    audio_duration_ms = len(samples) / int(sample_rate) * 1_000
    measured_stages_ms = sum(
        float(timings.get(name, 0.0))
        for name in (
            "phonemize_ms",
            "tokenize_ms",
            "onnx_inference_ms",
            "trim_audio_ms",
        )
    )
    return {
        "engine_create_ms": round(engine_create_ms, 1),
        "engine_create_other_ms": round(
            max(0.0, engine_create_ms - measured_stages_ms), 1
        ),
        "phonemize_ms": round(float(timings.get("phonemize_ms", 0.0)), 1),
        "tokenize_ms": round(float(timings.get("tokenize_ms", 0.0)), 1),
        "onnx_inference_ms": round(
            float(timings.get("onnx_inference_ms", 0.0)), 1
        ),
        "onnx_inference_count": int(timings.get("onnx_inference_count", 0)),
        "trim_audio_ms": round(float(timings.get("trim_audio_ms", 0.0)), 1),
        "audio_duration_ms": round(audio_duration_ms, 1),
        "real_time_factor": round(engine_create_ms / audio_duration_ms, 3)
        if audio_duration_ms > 0
        else None,
    }


def _median(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return round(statistics.median(values), 1) if values else None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="ONNX intra-op threads; 0 keeps ONNX Runtime's automatic default.",
    )
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument(
        "--nice", type=int, default=10, help="Lower CPU priority (0 disables)."
    )
    parser.add_argument("--voice", default=os.getenv("KOKORO_VOICE", "af_heart"))
    parser.add_argument("--lang", default=os.getenv("KOKORO_LANG_CODE", "en-us"))
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument(
        "--text", default=DEFAULT_TEXT, help="Text is never printed or saved."
    )
    parser.add_argument(
        "--model-path",
        default=os.getenv("KOKORO_MODEL_PATH", "/app/models/kokoro/model.onnx"),
    )
    parser.add_argument(
        "--voices-path",
        default=os.getenv("KOKORO_VOICES_PATH", "/app/models/kokoro/voices.bin"),
    )
    args = parser.parse_args()
    if args.threads < 0:
        parser.error("--threads must be zero (automatic) or a positive integer")
    if args.warmups < 0 or args.iterations < 1:
        parser.error("--warmups must be nonnegative and --iterations must be at least one")
    if not 0.5 <= args.speed <= 2.0:
        parser.error("--speed must be between 0.5 and 2.0")
    return args


def main() -> int:
    args = _parse_args()
    model_path = Path(args.model_path)
    voices_path = Path(args.voices_path)
    if not model_path.is_file():
        raise SystemExit(f"Kokoro model file not found: {model_path.name}")
    if not voices_path.is_file():
        raise SystemExit(f"Kokoro voices file not found: {voices_path.name}")

    try:
        import importlib.metadata

        import onnxruntime as ort
        from kokoro_onnx import Kokoro
    except ImportError as exc:
        raise SystemExit(f"Kokoro benchmark dependencies are unavailable: {exc}") from exc

    effective_nice = _lower_process_priority(args.nice)
    session_options = ort.SessionOptions()
    session_options.intra_op_num_threads = args.threads
    session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

    load_started_at = time.perf_counter()
    session = ort.InferenceSession(
        str(model_path),
        sess_options=session_options,
        providers=["CPUExecutionProvider"],
    )
    engine = Kokoro.from_session(session, str(voices_path))
    _install_engine_timing_hooks(engine)
    session_load_ms = round((time.perf_counter() - load_started_at) * 1_000, 1)

    warmup_started_at = time.perf_counter()
    for _ in range(args.warmups):
        engine.create(args.text, voice=args.voice, speed=args.speed, lang=args.lang)
    warmup_ms = round((time.perf_counter() - warmup_started_at) * 1_000, 1)

    runs = [
        _timed_create(
            engine,
            text=args.text,
            voice=args.voice,
            speed=args.speed,
            lang=args.lang,
        )
        for _ in range(args.iterations)
    ]
    try:
        providers = session.get_providers()
    except Exception:
        providers = []

    package_versions: dict[str, str] = {}
    for package in ("kokoro-onnx", "onnxruntime"):
        try:
            package_versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            package_versions[package] = "unknown"

    report = {
        "benchmark": "kokoro_cpu_warm_inference",
        "versions": package_versions,
        "model": {
            "filename": model_path.name,
            "size_bytes": model_path.stat().st_size,
            "voices_filename": voices_path.name,
            "voices_size_bytes": voices_path.stat().st_size,
        },
        "configuration": {
            "requested_intra_op_threads": args.threads,
            "execution_mode": "ORT_SEQUENTIAL",
            "provider": providers,
            "voice": args.voice,
            "language": args.lang,
            "speed": args.speed,
            "text_word_count": len(args.text.split()),
            "text_character_count": len(args.text),
            "warmups": args.warmups,
            "iterations": args.iterations,
        },
        "host": {
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "cpu_capacity": _cpu_capacity_timings(),
            "effective_nice": effective_nice,
        },
        "setup": {
            "session_load_ms": session_load_ms,
            "warmup_total_ms": warmup_ms,
        },
        "median": {
            key: _median(runs, key)
            for key in (
                "engine_create_ms",
                "engine_create_other_ms",
                "phonemize_ms",
                "tokenize_ms",
                "onnx_inference_ms",
                "trim_audio_ms",
                "audio_duration_ms",
                "real_time_factor",
            )
        },
        "runs": runs,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
