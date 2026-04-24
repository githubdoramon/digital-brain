from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from statistics import mean
from time import perf_counter
from typing import Any

from evals.flows import EVAL_FLOWS
from evals.types import EvalFlowDefinition
from llm_helpers import warm_chat_model
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

_FLOW_MAP: dict[str, EvalFlowDefinition] = {flow.flow_id: flow for flow in EVAL_FLOWS}


def get_eval_flow(flow_id: str) -> EvalFlowDefinition | None:
    return _FLOW_MAP.get(str(flow_id or "").strip())


def list_eval_flows() -> list[dict[str, Any]]:
    return [
        {
            "flow_id": flow.flow_id,
            "label": flow.label,
            "description": flow.description,
            "case_count": len(flow.cases),
            "cases": [
                {
                    "case_id": case.case_id,
                    "title": case.title,
                    "description": case.description,
                }
                for case in flow.cases
            ],
        }
        for flow in EVAL_FLOWS
    ]


async def _execute_case(flow: EvalFlowDefinition, case: Any, llm_model: str | None, user_email: str) -> dict[str, Any]:
    result = flow.execute_case(case, llm_model, user_email)
    if inspect.isawaitable(result):
        result = await result
    return result


async def _emit_progress(
    callback: Callable[[dict[str, Any]], Any | Awaitable[Any]] | None,
    payload: dict[str, Any],
) -> None:
    if callback is None:
        return
    result = callback(payload)
    if inspect.isawaitable(result):
        await result


async def run_eval_flow(
    *,
    flow_id: str,
    llm_model: str | None,
    repetitions: int,
    user_email: str,
    discard_first_attempt: bool = True,
    progress_callback: Callable[[dict[str, Any]], Any | Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    flow = get_eval_flow(flow_id)
    if flow is None:
        raise ValueError(f"Unknown eval flow: {flow_id}")

    normalized_repetitions = max(1, min(int(repetitions), 20))
    effective_discard_first_attempt = bool(discard_first_attempt and normalized_repetitions > 1)
    warmup: dict[str, Any] = {
        "attempted": False,
        "performed": False,
        "model": llm_model,
    }

    if llm_model:
        warmup["attempted"] = True
        warm_start = perf_counter()
        try:
            warmup["performed"] = warm_chat_model(llm_model)
        except Exception as exc:
            warmup["error"] = str(exc)
            logger.warning(
                "[evals.run] Failed to warm requested model=%r before benchmark: %s",
                llm_model,
                exc,
                exc_info=exc,
            )
        finally:
            warmup["duration_ms"] = (perf_counter() - warm_start) * 1000

    run_started = perf_counter()
    case_results: list[dict[str, Any]] = []
    total_attempts = 0
    passed_attempts = 0
    durations: list[float] = []
    measured_attempts = 0
    discarded_attempts = 0

    for case in flow.cases:
        attempts: list[dict[str, Any]] = []
        case_durations: list[float] = []
        case_passed_attempts = 0
        summary_variants: set[str] = set()

        for attempt_index in range(1, normalized_repetitions + 1):
            await _emit_progress(
                progress_callback,
                {
                    "current_case": len(case_results) + 1,
                    "total_cases": len(flow.cases),
                    "current_attempt": total_attempts + 1,
                    "total_attempts": len(flow.cases) * normalized_repetitions,
                    "current_case_id": case.case_id,
                    "current_case_title": case.title,
                    "attempt_in_case": attempt_index,
                    "repetitions": normalized_repetitions,
                    "status": "running",
                },
            )
            started = perf_counter()
            output = await _execute_case(flow, case, llm_model, user_email)
            duration_ms = (perf_counter() - started) * 1000
            score = flow.score_case(case, output)
            summary = flow.summarize_output(output)
            discarded = bool(effective_discard_first_attempt and attempt_index == 1)
            if not discarded:
                summary_variants.add(json.dumps(summary, sort_keys=True, default=str))

            attempt = {
                "attempt": attempt_index,
                "duration_ms": duration_ms,
                "passed": bool(score.get("passed")),
                "notes": score.get("notes") or [],
                "summary": summary,
                "output": output,
                "discarded": discarded,
            }
            attempts.append(attempt)
            total_attempts += 1
            if discarded:
                discarded_attempts += 1
                continue

            measured_attempts += 1
            case_durations.append(duration_ms)
            durations.append(duration_ms)
            if attempt["passed"]:
                case_passed_attempts += 1
                passed_attempts += 1

        measured_case_attempts = len(case_durations)
        case_results.append(
            {
                "case_id": case.case_id,
                "title": case.title,
                "description": case.description,
                "input": case.input,
                "expected": case.expected,
                "metrics": {
                    "attempts": measured_case_attempts,
                    "total_attempts": normalized_repetitions,
                    "discarded_attempts": normalized_repetitions - measured_case_attempts,
                    "passed_attempts": case_passed_attempts,
                    "pass_rate": (case_passed_attempts / measured_case_attempts)
                    if measured_case_attempts
                    else 0.0,
                    "avg_duration_ms": mean(case_durations) if case_durations else 0.0,
                    "variant_count": len(summary_variants),
                },
                "attempts": attempts,
            }
        )

    total_duration_ms = (perf_counter() - run_started) * 1000
    await _emit_progress(
        progress_callback,
        {
            "current_case": len(flow.cases),
            "total_cases": len(flow.cases),
            "current_attempt": total_attempts,
            "total_attempts": len(flow.cases) * normalized_repetitions,
            "current_case_id": case_results[-1]["case_id"] if case_results else None,
            "current_case_title": case_results[-1]["title"] if case_results else None,
            "status": "completed",
        },
    )
    return {
        "flow": {
            "flow_id": flow.flow_id,
            "label": flow.label,
            "description": flow.description,
            "case_count": len(flow.cases),
        },
        "llm_model": llm_model,
        "repetitions": normalized_repetitions,
        "discard_first_attempt": effective_discard_first_attempt,
        "warmup": warmup,
        "summary": {
            "total_attempts": total_attempts,
            "measured_attempts": measured_attempts,
            "discarded_attempts": discarded_attempts,
            "passed_attempts": passed_attempts,
            "pass_rate": (passed_attempts / measured_attempts) if measured_attempts else 0.0,
            "avg_duration_ms": mean(durations) if durations else 0.0,
            "total_duration_ms": total_duration_ms,
        },
        "cases": case_results,
    }
