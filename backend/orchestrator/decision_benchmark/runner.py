from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

from decision_benchmark.providers import headers_for, predict
from decision_benchmark.reporting import summarize_groups, write_reports
from decision_benchmark.tasks import DATA_RULE, expected_answers, questions_for
from decision_benchmark.types import Case, InvalidResponse, ModelConfig


def plan(cases: list[Case], models: list[ModelConfig], repetitions: int, warmups: int, seed: int):
    rng = random.Random(seed)
    for task in dict.fromkeys(case.task for case in cases):
        case = next(case for case in cases if case.task == task)
        for repeat in range(warmups):
            for model in models:
                yield "warmup", repeat + 1, model, case
    for repeat in range(repetitions):
        order = list(cases)
        rng.shuffle(order)
        for index, case in enumerate(order):
            offset = (repeat + index) % len(models)
            for model in models[offset:] + models[:offset]:
                yield "measured", repeat + 1, model, case


def attempt(
    client: httpx.Client, model: ModelConfig, case: Case, headers: dict[str, str]
) -> dict[str, Any]:
    expected = expected_answers(case)
    row = {
        "model_id": model.id,
        "case_id": case.id,
        "task": case.task,
        "language": case.language,
        "expected": expected,
        "answers": {},
        "confidence": {},
        "probabilities": {},
        "usage": {},
        "error": None,
    }
    started = perf_counter()
    try:
        prediction = predict(client, model, case, headers)
        row.update(asdict(prediction))
    except httpx.HTTPStatusError as exc:
        row["error"] = f"http_{exc.response.status_code}"
    except httpx.TimeoutException:
        row["error"] = "timeout"
    except httpx.RequestError:
        row["error"] = "connection_error"
    except InvalidResponse:
        row["error"] = "invalid_response"
    row["duration_ms"] = round((perf_counter() - started) * 1000, 3)
    row["question_passed"] = {
        key: row["answers"].get(key) == value for key, value in expected.items()
    }
    row["passed"] = row["error"] is None and all(row["question_passed"].values())
    return row


def run_benchmark(
    *,
    cases: list[Case],
    models: list[ModelConfig],
    output: Path,
    repetitions: int,
    warmups: int,
    seed: int,
    suite_hash: str,
    config_hash: str,
    progress: Callable[[dict[str, Any], int, int], None] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    # Resolve all selected credentials before creating output or making any requests.
    headers = {model.id: headers_for(model) for model in models}
    output.mkdir(parents=True, exist_ok=False)
    jobs = list(plan(cases, models, repetitions, warmups, seed))
    contract = {
        "data_rule": DATA_RULE,
        "cases": [
            {
                "id": case.id,
                "state": case.input,
                "questions": {key: asdict(q) for key, q in questions_for(case).items()},
            }
            for case in cases
        ],
    }
    report = {
        "version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "suite_sha256": suite_hash,
        "config_sha256": config_hash,
        "contract_sha256": hashlib.sha256(
            json.dumps(contract, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "settings": {
            "repetitions": repetitions,
            "warmups_per_task": warmups,
            "seed": seed,
            "concurrency": 1,
        },
        "models": [
            {
                "id": model.id,
                "protocol": model.protocol,
                "model": model.model,
                "structured_output": model.structured_output
                if model.protocol == "openai"
                else None,
            }
            for model in models
        ],
        "planned_measured_attempts": len(cases) * len(models) * repetitions,
        "planned_warmup_attempts": len(jobs) - len(cases) * len(models) * repetitions,
        "completed": False,
    }
    rows: list[dict[str, Any]] = []
    seen_models = set()
    owned_client = client is None
    # No automatic retries or redirects, and no model-specific default headers on this client.
    http_client = client if client is not None else httpx.Client(follow_redirects=False)
    interrupted = False
    try:
        with (output / "attempts.jsonl").open("x", encoding="utf-8") as stream:
            for index, (phase, repeat, model, case) in enumerate(jobs, 1):
                row = attempt(http_client, model, case, headers[model.id])
                row.update(
                    phase=phase,
                    repetition=repeat,
                    first_request=model.id not in seen_models,
                    sequence=index,
                )
                seen_models.add(model.id)
                rows.append(row)
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                if progress:
                    progress(row, index, len(jobs))
        report["completed"] = True
    except KeyboardInterrupt:
        interrupted = True
    finally:
        if owned_client:
            http_client.close()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["interrupted"] = interrupted
        report["summaries"] = summarize_groups(rows, [model.id for model in models])
        write_reports(output, report)
    return report
