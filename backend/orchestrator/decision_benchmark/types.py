from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelConfig:
    id: str
    protocol: str
    model: str
    url: str
    api_key_env: str | None = None
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    headers_env: dict[str, str] = field(default_factory=dict)
    request_options: dict[str, Any] = field(default_factory=dict)
    structured_output: str = "json_schema"
    timeout_seconds: float = 60


@dataclass(frozen=True)
class Case:
    id: str
    task: str
    language: str
    input: dict[str, Any]
    expected: dict[str, Any]


@dataclass(frozen=True)
class Question:
    instructions: str
    criteria: dict[str, str]


@dataclass(frozen=True)
class Prediction:
    answers: dict[str, str]
    confidence: dict[str, float] = field(default_factory=dict)
    probabilities: dict[str, dict[str, float]] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)


class InvalidResponse(ValueError):
    """Provider output did not conform to the declared answer space."""
