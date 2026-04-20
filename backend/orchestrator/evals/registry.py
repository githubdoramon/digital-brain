from __future__ import annotations

import inspect
import json
from statistics import mean
from time import perf_counter
from typing import Any

from evals.flows import EVAL_FLOWS
from evals.types import EvalFlowDefinition

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


async def run_eval_flow(
    *,
    flow_id: str,
    llm_model: str | None,
    repetitions: int,
    user_email: str,
) -> dict[str, Any]:
    flow = get_eval_flow(flow_id)
    if flow is None:
        raise ValueError(f"Unknown eval flow: {flow_id}")

    normalized_repetitions = max(1, min(int(repetitions), 20))
    run_started = perf_counter()
    case_results: list[dict[str, Any]] = []
    total_attempts = 0
    passed_attempts = 0
    durations: list[float] = []

    for case in flow.cases:
        attempts: list[dict[str, Any]] = []
        case_durations: list[float] = []
        case_passed_attempts = 0
        summary_variants: set[str] = set()

        for attempt_index in range(1, normalized_repetitions + 1):
            started = perf_counter()
            output = await _execute_case(flow, case, llm_model, user_email)
            duration_ms = (perf_counter() - started) * 1000
            score = flow.score_case(case, output)
            summary = flow.summarize_output(output)
            summary_variants.add(json.dumps(summary, sort_keys=True, default=str))

            attempt = {
                "attempt": attempt_index,
                "duration_ms": duration_ms,
                "passed": bool(score.get("passed")),
                "notes": score.get("notes") or [],
                "summary": summary,
                "output": output,
            }
            attempts.append(attempt)
            case_durations.append(duration_ms)
            durations.append(duration_ms)
            total_attempts += 1
            if attempt["passed"]:
                case_passed_attempts += 1
                passed_attempts += 1

        case_results.append(
            {
                "case_id": case.case_id,
                "title": case.title,
                "description": case.description,
                "input": case.input,
                "expected": case.expected,
                "metrics": {
                    "attempts": normalized_repetitions,
                    "passed_attempts": case_passed_attempts,
                    "pass_rate": case_passed_attempts / normalized_repetitions,
                    "avg_duration_ms": mean(case_durations) if case_durations else 0.0,
                    "variant_count": len(summary_variants),
                },
                "attempts": attempts,
            }
        )

    total_duration_ms = (perf_counter() - run_started) * 1000
    return {
        "flow": {
            "flow_id": flow.flow_id,
            "label": flow.label,
            "description": flow.description,
            "case_count": len(flow.cases),
        },
        "llm_model": llm_model,
        "repetitions": normalized_repetitions,
        "summary": {
            "total_attempts": total_attempts,
            "passed_attempts": passed_attempts,
            "pass_rate": (passed_attempts / total_attempts) if total_attempts else 0.0,
            "avg_duration_ms": mean(durations) if durations else 0.0,
            "total_duration_ms": total_duration_ms,
        },
        "cases": case_results,
    }
