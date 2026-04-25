from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    title: str
    input: dict[str, Any]
    expected: dict[str, Any]
    description: str | None = None
    response_json_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class EvalLlmRequestOptions:
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class EvalRunConfig:
    llm_model: str | None
    user_email: str
    timeout_seconds: int
    request_options: EvalLlmRequestOptions = field(default_factory=EvalLlmRequestOptions)


EvalExecutor = Callable[[EvalCase, EvalRunConfig], dict[str, Any] | Awaitable[dict[str, Any]]]
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
