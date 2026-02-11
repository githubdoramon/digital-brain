"""Adaptive model and timeout policy for bounded-agent LLM calls."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LLMCallPolicy:
    """Resolved model policy for a single LLM request."""

    model: str | None
    timeout: int
    profile: str
    rationale: str


def _parse_int(value: str | None, default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _question_complexity(question: str) -> int:
    text = str(question or "").strip().lower()
    if not text:
        return 0
    score = 0
    words = text.split()
    if len(words) >= 16:
        score += 1
    if len(words) >= 32:
        score += 1
    if any(token in text for token in ("compare", "analy", "plan", "strategy", "tradeoff")):
        score += 1
    if any(token in text for token in ("why", "how", "versus", "vs", "architecture")):
        score += 1
    return score


def select_llm_call_policy(
    *,
    question: str,
    state: Any,
    tools_count: int,
    default_model: str | None,
    default_timeout: int,
) -> LLMCallPolicy:
    """Choose model + timeout for the next agent LLM call."""
    fast_model = os.getenv("LLM_CHAT_MODEL_FAST", "").strip() or default_model
    smart_model = os.getenv("LLM_CHAT_MODEL_SMART", "").strip() or default_model
    complexity_threshold = _parse_int(os.getenv("AGENT_MODEL_ROUTING_COMPLEXITY_THRESHOLD"), 3)
    step_threshold = _parse_int(os.getenv("AGENT_MODEL_ROUTING_STEP_THRESHOLD"), 4)
    timeout_boost_seconds = _parse_int(os.getenv("AGENT_MODEL_ROUTING_TIMEOUT_BOOST_SECONDS"), 30)

    complexity = _question_complexity(question)
    if str(getattr(state, "route_confidence_tier", "")).strip().lower() == "low":
        complexity += 1
    if int(getattr(state, "step_count", 0) or 0) >= step_threshold:
        complexity += 1
    if int(getattr(state, "tool_calls_count", 0) or 0) >= 4:
        complexity += 1
    if tools_count >= 8:
        complexity += 1

    if complexity >= complexity_threshold:
        return LLMCallPolicy(
            model=smart_model,
            timeout=max(default_timeout, default_timeout + timeout_boost_seconds),
            profile="smart",
            rationale=f"complexity={complexity}",
        )

    reduced_timeout = max(30, int(default_timeout * 0.85))
    return LLMCallPolicy(
        model=fast_model,
        timeout=reduced_timeout,
        profile="fast",
        rationale=f"complexity={complexity}",
    )
