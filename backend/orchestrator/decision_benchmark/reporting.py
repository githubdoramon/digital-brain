from __future__ import annotations

import csv
import json
import math
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low, high = math.floor(index), math.ceil(index)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (index - low), 3)


def latencies(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [row["duration_ms"] for row in rows]
    return {
        "count": len(values),
        "mean_ms": round(mean(values), 3) if values else None,
        "p50_ms": percentile(values, 0.5),
        "p95_ms": percentile(values, 0.95),
        "min_ms": min(values) if values else None,
        "max_ms": max(values) if values else None,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [row for row in rows if row["phase"] == "measured"]
    valid = [row for row in measured if row["error"] is None]
    correct = [row for row in valid if row["passed"]]
    total_decisions = sum(len(row["expected"]) for row in measured)
    decision_correct = sum(sum(row["question_passed"].values()) for row in measured)
    confusion: dict[str, dict[str, dict[str, int]]] = {}
    for row in measured:
        for key, expected in row["expected"].items():
            actual = row["answers"].get(key, "__error__")
            counts = confusion.setdefault(key, {}).setdefault(expected, {})
            counts[actual] = counts.get(actual, 0) + 1
    # Confidence is provider-supplied, not an empirical probability of correctness.
    selective = []
    with_confidence = [row for row in valid if len(row["confidence"]) == len(row["expected"])]
    for threshold in (0.5, 0.8, 0.9):
        accepted = [row for row in with_confidence if min(row["confidence"].values()) >= threshold]
        selective.append(
            {
                "threshold": threshold,
                "eligible_attempts": len(with_confidence),
                "accepted_attempts": len(accepted),
                "coverage": len(accepted) / len(measured) if measured and with_confidence else None,
                "accuracy": sum(row["passed"] for row in accepted) / len(accepted)
                if accepted
                else None,
            }
        )
    return {
        "attempts": len(measured),
        "unique_cases": len({row["case_id"] for row in measured}),
        "correct": len(correct),
        "errors": len(measured) - len(valid),
        "error_counts": dict(Counter(row["error"] for row in measured if row["error"])),
        "accuracy": len(correct) / len(measured) if measured else None,
        "valid_answer_accuracy": len(correct) / len(valid) if valid else None,
        "decision_accuracy": decision_correct / total_decisions if total_decisions else None,
        "latency_all": latencies(measured),
        "latency_valid": latencies(valid),
        "latency_correct": latencies(correct),
        "warmup_latency": latencies([row for row in rows if row["phase"] == "warmup"]),
        "first_request_latency": latencies([row for row in rows if row["first_request"]]),
        "confusion": confusion,
        "confidence_slices": selective,
    }


def summarize_groups(rows: list[dict[str, Any]], model_ids: list[str]) -> dict[str, Any]:
    result = {}
    for model_id in model_ids:
        model_rows = [row for row in rows if row["model_id"] == model_id]
        tasks = sorted({row["task"] for row in model_rows})
        languages = sorted({row["language"] for row in model_rows})
        result[model_id] = {
            "overall": summarize(model_rows),
            "tasks": {
                task: summarize([row for row in model_rows if row["task"] == task])
                for task in tasks
            },
            "languages": {
                lang: summarize([row for row in model_rows if row["language"] == lang])
                for lang in languages
            },
        }
    return result


def summary_lines(summaries: dict[str, Any]) -> list[str]:
    lines = [
        f"{'Model / task':<44} {'Correct':>9} {'Accuracy':>9} {'Errors':>7} {'p50 ms':>10} {'p95 ms':>10}"
    ]
    for model_id, groups in summaries.items():
        for task, data in [("ALL", groups["overall"]), *groups["tasks"].items()]:
            p50, p95 = data["latency_valid"]["p50_ms"], data["latency_valid"]["p95_ms"]
            accuracy = f"{100 * data['accuracy']:.1f}%" if data["accuracy"] is not None else "n/a"
            median = f"{p50:.1f}" if p50 is not None else "n/a"
            tail = f"{p95:.1f}" if p95 is not None else "n/a"
            lines.append(
                f"{model_id + ' / ' + task:<44} {str(data['correct']) + '/' + str(data['attempts']):>9} {accuracy:>9} {data['errors']:>7} {median:>10} {tail:>10}"
            )
    return lines


def write_reports(output: Path, report: dict[str, Any]) -> None:
    (output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with (output / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "model_id",
                "scope",
                "name",
                "attempts",
                "unique_cases",
                "correct",
                "accuracy",
                "decision_accuracy",
                "errors",
                "mean_ms",
                "p50_ms",
                "p95_ms",
            ],
        )
        writer.writeheader()
        for model_id, groups in report["summaries"].items():
            for scope, group in (
                ("overall", {"all": groups["overall"]}),
                ("task", groups["tasks"]),
                ("language", groups["languages"]),
            ):
                for name, stats in group.items():
                    writer.writerow(
                        {
                            "model_id": model_id,
                            "scope": scope,
                            "name": name,
                            **{
                                key: stats[key]
                                for key in (
                                    "attempts",
                                    "unique_cases",
                                    "correct",
                                    "accuracy",
                                    "decision_accuracy",
                                    "errors",
                                )
                            },
                            **{
                                key: stats["latency_valid"][key]
                                for key in ("mean_ms", "p50_ms", "p95_ms")
                            },
                        }
                    )
    (output / "summary.txt").write_text(
        "\n".join(summary_lines(report["summaries"])) + "\n", encoding="utf-8"
    )
