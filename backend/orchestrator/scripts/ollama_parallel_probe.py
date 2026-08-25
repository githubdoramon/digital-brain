#!/usr/bin/env python3
"""Probe Ollama multi-model warmup and parallel request behavior.

Run this on the Ollama server, for example:

    python3 ollama_parallel_probe.py --models qwen3.5:4b gpt-oss:20b

The script uses only the Python standard library. It warms the selected models
with Ollama's native API, waits for them to appear in /api/ps, then sends
OpenAI-compatible chat requests. The first model is always called with reasoning
effort none. The second and later models are called with reasoning efforts
none/low/medium/high.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any
from urllib.parse import urlparse, urlunparse

DEFAULT_PROMPT = """Analyze this operational scenario and respond in 6 concise bullets:
A local personal-memory application uses two LLM profiles, one fast model for routing
and one smart model for command extraction. Startup warms both models through Ollama.
At runtime, users report that parallel requests are much slower than expected.
Identify likely bottlenecks, what metrics would distinguish model loading from
inference contention, and what configuration changes you would try first."""

EVENT_EXTRACTION_PROMPT = """You are extracting structured information from a user's event description to create a memory entry.

Current context:
- UTC now: 2026-06-11T10:30:00+00:00
- User timezone: Europe/Lisbon
- The user is the narrator and owner of the memory graph.

Known facts about this user:
- Prefers metric units.
- Has a recurring workout routine.
- Usually records personal events after they happen.

Event description:
"Last Tuesday at 10am I trained back and biceps with Jordan Example at his gym, then we discussed switching to a three day split and tracking progress monthly."

Conversation messages (JSON array, most recent last):
[{"role":"user","content":"Last Tuesday at 10am I trained back and biceps with Jordan Example at his gym, then we discussed switching to a three day split and tracking progress monthly."}]

Extract:
1. A brief title, 5-10 words.
2. A detailed summary.
3. Event start date/time. Interpret "last Tuesday" relative to the current context.
4. Optional end date/time if present.
5. Location/place name if present.
6. Document references if present.
7. Relevant tags.
8. Event types chosen from: generic, meeting, communication, task, creation, consumption, travel, personal, system, financial, observation, interaction, education, celebration, purchase, health.

People extraction is handled separately. Do not include any people/person list.
If critical non-person information is missing, set need_user_input. Otherwise set need_user_input to null.
Return only JSON matching the supplied schema."""

EVENT_EXTRACTION_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "event_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "need_user_input": {
                    "type": ["object", "null"],
                    "additionalProperties": True,
                },
                "title": {"type": ["string", "null"]},
                "summary": {"type": ["string", "null"]},
                "when": {"type": ["string", "null"]},
                "end_when": {"type": ["string", "null"]},
                "where": {"type": ["string", "null"]},
                "documents": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "types": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "need_user_input",
                "title",
                "summary",
                "when",
                "end_when",
                "where",
                "documents",
                "tags",
                "types",
            ],
            "additionalProperties": False,
        },
    },
}

EFFORTS = ("none", "low", "medium", "xhigh")


@dataclass
class RequestResult:
    model: str
    effort: str
    index: int
    ok: bool
    duration_s: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    content_chars: int = 0
    json_valid: bool | None = None
    error: str | None = None


@dataclass
class WarmupResult:
    model: str
    ok: bool
    duration_s: float
    error: str | None = None


def _normalize_native_base_url(base_url: str) -> str:
    candidate = str(base_url or "").strip().rstrip("/")
    if not candidate:
        candidate = "http://localhost:11434"
    parsed = urlparse(candidate)
    if parsed.path.rstrip("/") == "/v1":
        parsed = parsed._replace(path="")
    return urlunparse(parsed).rstrip("/")


def _openai_base_url(native_base_url: str) -> str:
    return f"{native_base_url.rstrip('/')}/v1"


def _json_request(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float,
) -> Any:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace").strip()
        detail = f"HTTP {exc.code} {exc.reason}"
        if error_body:
            detail = f"{detail}: {error_body}"
        raise RuntimeError(detail) from exc
    if not data:
        return None
    return json.loads(data.decode("utf-8"))


def ollama_version(base_url: str, timeout: float) -> str | None:
    try:
        data = _json_request("GET", f"{base_url}/api/version", timeout=timeout)
        if isinstance(data, dict):
            return str(data.get("version") or "").strip() or None
    except Exception:
        return None
    return None


def ollama_ps(base_url: str, timeout: float) -> list[dict[str, Any]]:
    data = _json_request("GET", f"{base_url}/api/ps", timeout=timeout)
    if not isinstance(data, dict):
        return []
    models = data.get("models")
    return models if isinstance(models, list) else []


def ollama_tags(base_url: str, timeout: float) -> list[str]:
    data = _json_request("GET", f"{base_url}/api/tags", timeout=timeout)
    if not isinstance(data, dict):
        return []
    models = data.get("models")
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        if name:
            names.append(name)
    return sorted(names)


def loaded_model_names(base_url: str, timeout: float) -> set[str]:
    names: set[str] = set()
    for item in ollama_ps(base_url, timeout):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        if name:
            names.add(name)
    return names


def print_loaded_models(base_url: str, timeout: float) -> None:
    try:
        models = ollama_ps(base_url, timeout)
    except Exception as exc:
        print(f"/api/ps unavailable: {exc}")
        return
    if not models:
        print("/api/ps: no models currently loaded")
        return
    print("/api/ps loaded models:")
    for item in models:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "?")
        size = item.get("size")
        size_vram = item.get("size_vram")
        expires_at = item.get("expires_at")
        details = []
        if isinstance(size, int):
            details.append(f"size={_bytes_to_gib(size):.2f} GiB")
        if isinstance(size_vram, int):
            details.append(f"vram={_bytes_to_gib(size_vram):.2f} GiB")
        if expires_at:
            details.append(f"expires_at={expires_at}")
        suffix = f" ({', '.join(details)})" if details else ""
        print(f"  - {name}{suffix}")


def _bytes_to_gib(value: int) -> float:
    return float(value) / (1024**3)


def _parse_keep_alive(value: str) -> str | int:
    normalized = str(value or "").strip()
    if normalized.lstrip("-").isdigit():
        return int(normalized)
    return normalized


def warm_model(
    *,
    base_url: str,
    model: str,
    keep_alive: str | int,
    timeout: float,
) -> WarmupResult:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
        "keep_alive": keep_alive,
    }
    started = time.perf_counter()
    try:
        _json_request("POST", f"{base_url}/api/chat", payload=payload, timeout=timeout)
        return WarmupResult(model=model, ok=True, duration_s=time.perf_counter() - started)
    except Exception as exc:
        return WarmupResult(
            model=model,
            ok=False,
            duration_s=time.perf_counter() - started,
            error=f"{type(exc).__name__}: {exc}",
        )


def wait_for_models_loaded(
    *,
    base_url: str,
    models: list[str],
    timeout: float,
    poll_interval: float,
) -> tuple[bool, set[str]]:
    expected = set(models)
    deadline = time.perf_counter() + timeout
    last_seen: set[str] = set()
    while time.perf_counter() < deadline:
        try:
            last_seen = loaded_model_names(base_url, timeout=min(5.0, poll_interval + 1.0))
        except Exception:
            last_seen = set()
        if expected.issubset(last_seen):
            return True, last_seen
        time.sleep(poll_interval)
    return expected.issubset(last_seen), last_seen


def chat_completion_request(
    *,
    base_url: str,
    model: str,
    effort: str | None,
    prompt: str,
    timeout: float,
    max_tokens: int | None,
    temperature: float,
    index: int,
    response_format: dict[str, Any] | None,
) -> RequestResult:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a precise performance-test assistant. Answer directly.",
            },
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "temperature": temperature,
    }
    if effort is not None:
        payload["reasoning_effort"] = effort
        payload["reasoning"] = {"effort": effort}
    else:
        payload["reasoning_effort"] = "none"
        payload["reasoning"] = {"effort": "none"}
    if max_tokens is not None and max_tokens > 0:
        payload["max_tokens"] = max_tokens
    if response_format:
        payload["response_format"] = response_format
    started = time.perf_counter()
    try:
        data = _json_request(
            "POST",
            f"{_openai_base_url(base_url)}/chat/completions",
            payload=payload,
            timeout=timeout,
        )
        duration = time.perf_counter() - started
        usage = data.get("usage") if isinstance(data, dict) else {}
        choices = data.get("choices") if isinstance(data, dict) else []
        content = ""
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else {}
            if isinstance(message, dict):
                content = str(message.get("content") or "")
        json_valid = None
        if response_format:
            try:
                parsed_content = json.loads(content)
                json_valid = isinstance(parsed_content, dict)
            except json.JSONDecodeError:
                json_valid = False
        return RequestResult(
            model=model,
            effort=effort or "none",
            index=index,
            ok=True,
            duration_s=duration,
            prompt_tokens=_int_or_none(usage.get("prompt_tokens") if isinstance(usage, dict) else None),
            completion_tokens=_int_or_none(
                usage.get("completion_tokens") if isinstance(usage, dict) else None
            ),
            total_tokens=_int_or_none(usage.get("total_tokens") if isinstance(usage, dict) else None),
            content_chars=len(content),
            json_valid=json_valid,
        )
    except Exception as exc:
        return RequestResult(
            model=model,
            effort=effort or "none",
            index=index,
            ok=False,
            duration_s=time.perf_counter() - started,
            error=f"{type(exc).__name__}: {exc}",
        )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class PsSampler:
    def __init__(self, *, base_url: str, selected_models: list[str], interval_s: float):
        self.base_url = base_url
        self.selected_models = set(selected_models)
        self.interval_s = max(0.1, interval_s)
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> PsSampler:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            try:
                names = loaded_model_names(self.base_url, timeout=min(5.0, self.interval_s + 1.0))
                selected_loaded = sorted(self.selected_models.intersection(names))
                self.samples.append(
                    {
                        "at": now,
                        "loaded": sorted(names),
                        "selected_loaded": selected_loaded,
                        "selected_loaded_count": len(selected_loaded),
                    }
                )
            except Exception as exc:
                self.samples.append({"at": now, "error": f"{type(exc).__name__}: {exc}"})
            self._stop.wait(self.interval_s)


def run_effort_batch(
    *,
    base_url: str,
    models: list[str],
    effort: str,
    prompt: str,
    request_timeout: float,
    max_tokens: int | None,
    temperature: float,
    requests_per_effort: int,
    ps_interval: float,
    response_format: dict[str, Any] | None,
) -> tuple[list[RequestResult], dict[str, Any]]:
    jobs: list[tuple[str, int, str | None]] = []
    for model_position, model in enumerate(models):
        model_effort = None if model_position == 0 else effort
        for index in range(1, requests_per_effort + 1):
            jobs.append((model, index, model_effort))

    started = time.perf_counter()
    with PsSampler(base_url=base_url, selected_models=models, interval_s=ps_interval) as sampler:
        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            futures = [
                executor.submit(
                    chat_completion_request,
                    base_url=base_url,
                    model=model,
                    effort=model_effort,
                    prompt=prompt,
                    timeout=request_timeout,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    index=index,
                    response_format=response_format,
                )
                for model, index, model_effort in jobs
            ]
            results = [future.result() for future in as_completed(futures)]
    wall_s = time.perf_counter() - started
    results.sort(key=lambda item: (item.model, item.index))
    batch_summary = summarize_batch(results, sampler.samples, wall_s, len(jobs), len(models))
    return results, batch_summary


def summarize_batch(
    results: list[RequestResult],
    samples: list[dict[str, Any]],
    wall_s: float,
    request_count: int,
    model_count: int,
) -> dict[str, Any]:
    ok_results = [item for item in results if item.ok]
    sum_request_s = sum(item.duration_s for item in results)
    max_loaded = 0
    all_selected_samples = 0
    usable_samples = 0
    for sample in samples:
        if "selected_loaded_count" not in sample:
            continue
        usable_samples += 1
        loaded_count = int(sample.get("selected_loaded_count") or 0)
        max_loaded = max(max_loaded, loaded_count)
        if loaded_count >= model_count:
            all_selected_samples += 1

    concurrency_factor = (sum_request_s / wall_s) if wall_s > 0 else 0.0
    all_loaded_pct = (all_selected_samples / usable_samples * 100.0) if usable_samples else 0.0
    parallel_signal = "weak"
    if request_count <= 1:
        parallel_signal = "not_applicable"
    elif concurrency_factor >= max(1.35, min(request_count, 2) * 0.75):
        parallel_signal = "strong"
    elif concurrency_factor >= 1.15:
        parallel_signal = "partial"

    return {
        "wall_s": wall_s,
        "request_count": request_count,
        "ok_count": len(ok_results),
        "error_count": len(results) - len(ok_results),
        "sum_request_s": sum_request_s,
        "concurrency_factor": concurrency_factor,
        "parallel_signal": parallel_signal,
        "ps_sample_count": usable_samples,
        "max_selected_models_loaded": max_loaded,
        "all_selected_models_loaded_pct": all_loaded_pct,
    }


def run_nvidia_smi() -> str | None:
    binary = shutil.which("nvidia-smi")
    if not binary:
        return None
    try:
        result = subprocess.run(
            [
                binary,
                "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return f"nvidia-smi failed: {type(exc).__name__}: {exc}"
    output = result.stdout.strip() or result.stderr.strip()
    return output or None


def parse_ollama_log(
    *,
    path: str | None,
    models: list[str],
    tail_bytes: int,
) -> dict[str, Any] | None:
    if not path:
        return None
    log_path = Path(path)
    if not log_path.exists():
        return {"error": f"log file not found: {path}"}
    try:
        with log_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - tail_bytes), os.SEEK_SET)
            text = handle.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    keywords = ("load", "loaded", "runner", "sched", "gpu", "vram", "unload", "parallel")
    lines = [line for line in text.splitlines() if line.strip()]
    model_summary: dict[str, dict[str, int]] = {}
    interesting_count = 0
    for model in models:
        lowered_model = model.lower()
        counts = dict.fromkeys(keywords, 0)
        for line in lines:
            lowered = line.lower()
            if lowered_model not in lowered:
                continue
            for keyword in keywords:
                if keyword in lowered:
                    counts[keyword] += 1
                    interesting_count += 1
        model_summary[model] = counts
    return {
        "path": str(log_path),
        "tail_bytes": tail_bytes,
        "line_count": len(lines),
        "model_keyword_counts": model_summary,
        "interesting_count": interesting_count,
    }


def print_results(
    *,
    models: list[str],
    warmups: list[WarmupResult],
    all_results: list[RequestResult],
    batch_summaries: dict[str, dict[str, Any]],
    log_summary: dict[str, Any] | None,
    started_at: float,
    finished_at: float,
) -> None:
    print("\n=== Warmup ===")
    for item in warmups:
        status = "ok" if item.ok else "failed"
        suffix = f" error={item.error}" if item.error else ""
        print(f"{item.model:32} {status:7} {item.duration_s:8.2f}s{suffix}")

    print("\n=== Request Timings ===")
    print(
        f"{'effort':8} {'model':32} {'idx':>3} {'status':8} {'duration':>9} "
        f"{'tokens':>8} {'chars':>7} {'json':>5} error"
    )
    for item in sorted(all_results, key=lambda row: (row.effort, row.model, row.index)):
        status = "ok" if item.ok else "failed"
        tokens = str(item.total_tokens) if item.total_tokens is not None else "-"
        json_status = "-" if item.json_valid is None else ("yes" if item.json_valid else "no")
        error = item.error or ""
        print(
            f"{item.effort:8} {item.model:32} {item.index:3d} {status:8} "
            f"{item.duration_s:8.2f}s {tokens:>8} {item.content_chars:7d} {json_status:>5} {error}"
        )

    print("\n=== Per Model Summary ===")
    for model in models:
        model_results = [item for item in all_results if item.model == model]
        ok_durations = [item.duration_s for item in model_results if item.ok]
        if ok_durations:
            json_results = [item.json_valid for item in model_results if item.json_valid is not None]
            json_suffix = ""
            if json_results:
                json_suffix = f" json_valid={sum(1 for item in json_results if item)}/{len(json_results)}"
            print(
                f"{model:32} ok={len(ok_durations)}/{len(model_results)} "
                f"min={min(ok_durations):.2f}s p50={median(ok_durations):.2f}s "
                f"avg={mean(ok_durations):.2f}s max={max(ok_durations):.2f}s"
                f"{json_suffix}"
            )
        else:
            print(f"{model:32} ok=0/{len(model_results)}")

    print("\n=== Per Effort Batch Summary ===")
    for effort in EFFORTS:
        summary = batch_summaries.get(effort)
        if not summary:
            continue
        print(
            f"{effort:8} wall={summary['wall_s']:.2f}s "
            f"sum_requests={summary['sum_request_s']:.2f}s "
            f"concurrency_factor={summary['concurrency_factor']:.2f} "
            f"parallel_signal={summary['parallel_signal']} "
            f"max_loaded={summary['max_selected_models_loaded']} "
            f"all_loaded_samples={summary['all_selected_models_loaded_pct']:.0f}% "
            f"errors={summary['error_count']}"
        )

    if log_summary:
        print("\n=== Ollama Log Summary ===")
        if "error" in log_summary:
            print(log_summary["error"])
        else:
            print(
                f"path={log_summary['path']} tail_bytes={log_summary['tail_bytes']} "
                f"lines={log_summary['line_count']}"
            )
            for model, counts in log_summary["model_keyword_counts"].items():
                non_zero = {key: value for key, value in counts.items() if value}
                print(f"{model:32} {non_zero or '{}'}")

    print("\n=== Interpretation Hints ===")
    print("- max_loaded lower than selected model count means Ollama did not keep all selected models loaded.")
    print("- concurrency_factor near 1.0 means requests likely serialized or contended heavily.")
    print("- concurrency_factor near request_count means responses overlapped well.")
    print("- If warmup is slow but timed requests are fast, the issue is mostly model loading.")
    print("- If xhigh effort is much slower while ps shows all models loaded, the issue is inference contention.")
    print(f"\nTotal elapsed: {finished_at - started_at:.2f}s")


def write_json_report(
    *,
    path: str,
    args: argparse.Namespace,
    version: str | None,
    warmups: list[WarmupResult],
    results: list[RequestResult],
    batch_summaries: dict[str, dict[str, Any]],
    log_summary: dict[str, Any] | None,
    nvidia_smi: str | None,
) -> None:
    report = {
        "args": vars(args),
        "ollama_version": version,
        "nvidia_smi": nvidia_smi,
        "warmups": [item.__dict__ for item in warmups],
        "requests": [item.__dict__ for item in results],
        "batch_summaries": batch_summaries,
        "log_summary": log_summary,
    }
    Path(path).write_text(json.dumps(report, indent=2), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark Ollama model warmup and parallel chat completion behavior."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("OLLAMA_BASE_URL") or os.getenv("LLM_BASE_URL") or "http://localhost:11434",
        help="Ollama base URL. Accepts native base or /v1 URL. Default: %(default)s",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        required=True,
        help="Model names to keep loaded and test, e.g. qwen3.5:4b gpt-oss:20b",
    )
    parser.add_argument("--keep-alive", default="-1", help="Ollama keep_alive for warmup.")
    parser.add_argument("--warmup-timeout", type=float, default=600.0)
    parser.add_argument("--request-timeout", type=float, default=600.0)
    parser.add_argument("--ps-timeout", type=float, default=120.0)
    parser.add_argument("--ps-interval", type=float, default=0.5)
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Optional output cap. By default no max_tokens is sent.",
    )
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument(
        "--requests-per-effort",
        type=int,
        default=1,
        help=(
            "Requests per model per reasoning effort. Default 1 gives 4 requests per model. "
            "Use 2 for heavier duplicate prompts."
        ),
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Prompt text for timed requests.",
    )
    parser.add_argument(
        "--scenario",
        choices=("generic", "event-extraction"),
        default="generic",
        help="Request shape to benchmark. event-extraction uses a JSON schema response_format.",
    )
    parser.add_argument("--prompt-file", help="Read timed request prompt from this file.")
    parser.add_argument("--ollama-log", help="Optional Ollama log file to summarize.")
    parser.add_argument("--log-tail-bytes", type=int, default=2_000_000)
    parser.add_argument("--json-out", help="Optional path for a JSON report.")
    parser.add_argument("--skip-warmup", action="store_true")
    parser.add_argument("--skip-nvidia-smi", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    base_url = _normalize_native_base_url(args.base_url)
    models = list(dict.fromkeys(args.models))
    keep_alive = _parse_keep_alive(args.keep_alive)
    response_format: dict[str, Any] | None = None
    prompt = EVENT_EXTRACTION_PROMPT if args.scenario == "event-extraction" else args.prompt
    if args.scenario == "event-extraction":
        response_format = EVENT_EXTRACTION_RESPONSE_FORMAT
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")

    print(f"Ollama base URL: {base_url}")
    print(f"Models: {', '.join(models)}")
    print(f"Scenario: {args.scenario}")
    version = ollama_version(base_url, timeout=10)
    if version:
        print(f"Ollama version: {version}")
    try:
        available_models = ollama_tags(base_url, timeout=10)
        missing_models = [model for model in models if model not in available_models]
        if missing_models:
            print("\nWARNING: These requested models are not listed by /api/tags:")
            for model in missing_models:
                print(f"  - {model}")
            print("Available models:")
            for model in available_models:
                print(f"  - {model}")
    except Exception as exc:
        print(f"\nWARNING: Could not read /api/tags: {type(exc).__name__}: {exc}")
    nvidia_smi = None if args.skip_nvidia_smi else run_nvidia_smi()
    if nvidia_smi:
        print("\n=== GPU Snapshot ===")
        print(nvidia_smi)

    started_at = time.perf_counter()
    print("\n=== Initial /api/ps ===")
    print_loaded_models(base_url, timeout=10)

    warmups: list[WarmupResult] = []
    warmed_model_names: list[str] = []
    if args.skip_warmup:
        print("\nSkipping warmup.")
        warmed_model_names = models
    else:
        print("\nWarming models in parallel...")
        with ThreadPoolExecutor(max_workers=len(models)) as executor:
            futures = [
                executor.submit(
                    warm_model,
                    base_url=base_url,
                    model=model,
                    keep_alive=keep_alive,
                    timeout=args.warmup_timeout,
                )
                for model in models
            ]
            warmups = [future.result() for future in as_completed(futures)]
        warmups.sort(key=lambda item: models.index(item.model))
        for item in warmups:
            status = "ok" if item.ok else "failed"
            print(f"  {item.model}: {status} in {item.duration_s:.2f}s")
            if item.error:
                print(f"    {item.error}")

        warmed_model_names = [item.model for item in warmups if item.ok]
        if warmed_model_names:
            all_loaded, seen = wait_for_models_loaded(
                base_url=base_url,
                models=warmed_model_names,
                timeout=args.ps_timeout,
                poll_interval=args.ps_interval,
            )
            print(
                "All successfully warmed models loaded: "
                f"{all_loaded} seen={', '.join(sorted(seen)) or '-'}"
            )
        else:
            print("No models warmed successfully; skipping /api/ps wait.")

    print("\n=== Post-warmup /api/ps ===")
    print_loaded_models(base_url, timeout=10)

    test_models = warmed_model_names
    if not test_models:
        print("\nNo models available for timed requests; aborting benchmark.")
        return 2
    if test_models != models:
        skipped = [model for model in models if model not in test_models]
        print(f"\nTimed requests will run only for warmed models: {', '.join(test_models)}")
        print(f"Skipping failed warmup models: {', '.join(skipped)}")

    all_results: list[RequestResult] = []
    batch_summaries: dict[str, dict[str, Any]] = {}
    for effort in EFFORTS:
        print(f"\nRunning first_model_effort=none other_model_effort={effort} ...")
        results, summary = run_effort_batch(
            base_url=base_url,
            models=test_models,
            effort=effort,
            prompt=prompt,
            request_timeout=args.request_timeout,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            requests_per_effort=max(1, args.requests_per_effort),
            ps_interval=args.ps_interval,
            response_format=response_format,
        )
        all_results.extend(results)
        batch_summaries[effort] = summary
        print(
            f"  wall={summary['wall_s']:.2f}s concurrency_factor="
            f"{summary['concurrency_factor']:.2f} signal={summary['parallel_signal']}"
        )

    log_summary = parse_ollama_log(
        path=args.ollama_log,
        models=models,
        tail_bytes=args.log_tail_bytes,
    )
    finished_at = time.perf_counter()
    print_results(
        models=test_models,
        warmups=warmups,
        all_results=all_results,
        batch_summaries=batch_summaries,
        log_summary=log_summary,
        started_at=started_at,
        finished_at=finished_at,
    )

    if args.json_out:
        write_json_report(
            path=args.json_out,
            args=args,
            version=version,
            warmups=warmups,
            results=all_results,
            batch_summaries=batch_summaries,
            log_summary=log_summary,
            nvidia_smi=nvidia_smi,
        )
        print(f"\nWrote JSON report: {args.json_out}")

    return 0 if all(item.ok for item in all_results) else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
