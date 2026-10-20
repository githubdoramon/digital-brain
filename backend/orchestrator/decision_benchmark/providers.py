"""Direct HTTP adapters; no production globals, retries, fallbacks or payload logs."""

from __future__ import annotations

import json
import math
import os
from typing import Any

import httpx

from decision_benchmark.json_utils import decode_json
from decision_benchmark.tasks import DATA_RULE, questions_for
from decision_benchmark.types import Case, InvalidResponse, ModelConfig, Prediction, Question


def headers_for(model: ModelConfig) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if model.api_key_env:
        secret = os.environ.get(model.api_key_env)
        if not secret:
            raise ValueError(f"{model.id}: set {model.api_key_env} before running")
        headers[model.auth_header] = model.auth_prefix + secret
    for header, variable in model.headers_env.items():
        value = os.environ.get(variable)
        if not value:
            raise ValueError(f"{model.id}: set {variable} before running")
        headers[header] = value
    if any("\r" in value or "\n" in value for value in headers.values()):
        raise ValueError(f"{model.id}: authentication contains a line break")
    try:
        for value in headers.values():
            value.encode("ascii")
    except UnicodeError:
        raise ValueError(f"{model.id}: authentication header values must be ASCII") from None
    return headers


def request_for(model: ModelConfig, case: Case) -> dict[str, Any]:
    questions = questions_for(case)
    typed = {
        key: {
            "type": "choice",
            "instructions": DATA_RULE + " " + q.instructions,
            "criteria": q.criteria,
        }
        for key, q in questions.items()
    }
    if model.protocol in {"jev", "this_that"}:
        return {"model": model.model, "state": case.input, "questions": typed}
    payload = {
        "model": model.model,
        "messages": [
            {
                "role": "system",
                "content": DATA_RULE + " Answer each question independently. Return only a JSON "
                "object mapping each question ID to exactly one of its criteria keys. "
                "Do not include reasoning, confidence or additional keys.",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"state": case.input, "questions": typed}, ensure_ascii=False
                ),
            },
        ],
        "stream": False,
        **model.request_options,
    }
    if model.structured_output == "json_schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "decision_benchmark",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        key: {"type": "string", "enum": list(q.criteria)}
                        for key, q in questions.items()
                    },
                    "required": list(questions),
                    "additionalProperties": False,
                },
            },
        }
    elif model.structured_output == "json_object":
        payload["response_format"] = {"type": "json_object"}
    return payload


def _probability(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= value <= 1
        or not math.isfinite(value)
    ):
        raise InvalidResponse("invalid_probability")
    return float(value)


def validate_answers(answers: Any, questions: dict[str, Question]) -> dict[str, str]:
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise InvalidResponse("answer_keys")
    for key, answer in answers.items():
        if not isinstance(answer, str) or answer not in questions[key].criteria:
            raise InvalidResponse("answer_outside_options")
    return answers


def parse_prediction(model: ModelConfig, case: Case, data: Any) -> Prediction:
    questions = questions_for(case)
    if not isinstance(data, dict):
        raise InvalidResponse("response_object")
    confidence: dict[str, float] = {}
    probabilities: dict[str, dict[str, float]] = {}
    if model.protocol == "openai":
        try:
            choice = data["choices"][0]
            if not isinstance(choice, dict):
                raise InvalidResponse("invalid_choice")
            # Incomplete/truncated completions must not masquerade as valid decisions.
            if choice.get("finish_reason") not in {None, "stop"}:
                raise InvalidResponse("incomplete_completion")
            message = choice["message"]
            if not isinstance(message, dict):
                raise InvalidResponse("invalid_message")
            if message.get("refusal"):
                raise InvalidResponse("refusal")
            content = message["content"]
            if not isinstance(content, str):
                raise InvalidResponse("content_not_text")
            content = content.strip()
            # Accept a single surrounding Markdown fence, without salvaging arbitrary prose.
            if content.startswith("```json\n") and content.endswith("\n```"):
                content = content[8:-4]
            elif content.startswith("```\n") and content.endswith("\n```"):
                content = content[4:-4]
            answers = decode_json(content)
        except (KeyError, IndexError, TypeError, ValueError):
            raise InvalidResponse("invalid_chat_completion") from None
    else:
        raw = data.get("answers")
        if not isinstance(raw, dict) or set(raw) != set(questions):
            raise InvalidResponse("answer_keys")
        answers = {}
        for key, question in questions.items():
            result = raw[key]
            if not isinstance(result, dict) or result.get("type") != "choice":
                raise InvalidResponse("answer_type")
            answers[key] = result.get("choice")
            confidence[key] = _probability(result.get("confidence"))
            distribution = result.get("probabilities")
            if not isinstance(distribution, dict) or set(distribution) != set(question.criteria):
                raise InvalidResponse("probability_keys")
            probabilities[key] = {label: _probability(p) for label, p in distribution.items()}
            if not math.isclose(sum(probabilities[key].values()), 1, abs_tol=0.01):
                raise InvalidResponse("probability_sum")
            if (
                not isinstance(answers[key], str)
                or answers[key] not in probabilities[key]
                or probabilities[key][answers[key]] < max(probabilities[key].values())
            ):
                raise InvalidResponse("choice_not_argmax")
    answers = validate_answers(answers, questions)
    usage = data.get("usage", {})
    safe_usage = {
        key: usage[key]
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "input_tokens",
            "output_tokens",
            "total_tokens",
        )
        if isinstance(usage, dict) and type(usage.get(key)) is int and usage[key] >= 0
    }
    return Prediction(answers, confidence, probabilities, safe_usage)


def predict(
    client: httpx.Client, model: ModelConfig, case: Case, headers: dict[str, str]
) -> Prediction:
    response = client.post(
        model.url, headers=headers, json=request_for(model, case), timeout=model.timeout_seconds
    )
    response.raise_for_status()
    try:
        data = decode_json(response.text)
    except ValueError:
        raise InvalidResponse("invalid_json") from None
    return parse_prediction(model, case, data)
