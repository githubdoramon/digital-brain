from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    title: str
    input: dict[str, Any]
    expected: dict[str, Any]
    description: str | None = None


EvalExecutor = Callable[[EvalCase, str | None, str], dict[str, Any] | Awaitable[dict[str, Any]]]
EvalScorer = Callable[[EvalCase, dict[str, Any]], dict[str, Any]]
EvalSummarizer = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class EvalFlowDefinition:
    flow_id: str
    label: str
    description: str
    cases: list[EvalCase]
    execute_case: EvalExecutor
    score_case: EvalScorer
    summarize_output: EvalSummarizer
