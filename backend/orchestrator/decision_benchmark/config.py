from __future__ import annotations

import math
import re
from pathlib import Path
from urllib.parse import urlsplit

from decision_benchmark.json_utils import decode_json
from decision_benchmark.tasks import TASKS, validate_case
from decision_benchmark.types import Case, ModelConfig

DEFAULT_CASES = Path(__file__).with_name("cases.json")
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


def read_json(path: Path):
    try:
        return decode_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ValueError(f"Cannot read valid JSON from {path.name}") from None


def _id(value, label: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ValueError(f"{label}: use a short ID containing letters, digits, _, - or .")
    return value


def load_cases(path: Path = DEFAULT_CASES) -> list[Case]:
    data = read_json(path)
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("cases"), list)
    ):
        raise ValueError("Case file must contain version: 1 and a cases list")
    cases = []
    seen = set()
    for row in data["cases"]:
        if not isinstance(row, dict):
            raise ValueError("Each case must be an object")
        case_id = _id(row.get("id"), "case.id")
        if case_id in seen:
            raise ValueError(f"Duplicate case ID: {case_id}")
        seen.add(case_id)
        if row.get("task") not in TASKS:
            raise ValueError(f"{case_id}: unknown task")
        if not isinstance(row.get("input"), dict) or not isinstance(row.get("expected"), dict):
            raise ValueError(f"{case_id}: input and expected must be objects")
        case = Case(
            case_id,
            row["task"],
            _id(row.get("language", "en"), "language"),
            row["input"],
            row["expected"],
        )
        validate_case(case)
        cases.append(case)
    if not cases:
        raise ValueError("Case suite must not be empty")
    return cases


def load_models(path: Path) -> list[ModelConfig]:
    data = read_json(path)
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("models"), list)
    ):
        raise ValueError("Model config must contain version: 1 and a models list")
    models = []
    seen = set()
    allowed = set(ModelConfig.__dataclass_fields__)
    reserved = {
        "model",
        "messages",
        "stream",
        "state",
        "questions",
        "response_format",
        "tools",
        "tool_choice",
    }
    for row in data["models"]:
        if not isinstance(row, dict) or set(row) - allowed:
            raise ValueError("Model config contains an unknown field")
        model_id = _id(row.get("id"), "model.id")
        if model_id in seen:
            raise ValueError(f"Duplicate model ID: {model_id}")
        seen.add(model_id)
        if not isinstance(row.get("protocol"), str) or row["protocol"] not in {
            "openai",
            "jev",
            "this_that",
        }:
            raise ValueError(f"{model_id}: protocol must be openai, jev or this_that")
        if not isinstance(row.get("model"), str) or not row["model"].strip():
            raise ValueError(f"{model_id}: model is required")
        try:
            url = urlsplit(row.get("url", ""))
            valid_url = (
                url.scheme in {"http", "https"}
                and url.hostname
                and not (url.username or url.password or url.query or url.fragment)
            )
        except (ValueError, TypeError, AttributeError):
            valid_url = False
        if not valid_url:
            raise ValueError(
                f"{model_id}: use a full HTTP(S) endpoint URL without credentials/query/fragment"
            )
        options = row.get("request_options", {})
        if not isinstance(options, dict) or set(options) & reserved:
            raise ValueError(f"{model_id}: request_options cannot replace the benchmark contract")
        if row.get("protocol") != "openai" and options:
            raise ValueError(f"{model_id}: request_options apply only to openai endpoints")
        headers = row.get("headers_env", {})
        if not isinstance(headers, dict) or any(
            not isinstance(k, str)
            or not re.fullmatch(r"[A-Za-z0-9-]+", k)
            or not isinstance(v, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v)
            for k, v in headers.items()
        ):
            raise ValueError(
                f"{model_id}: headers_env maps header names to environment variable names"
            )
        if row.get("api_key_env") is not None and (
            not isinstance(row["api_key_env"], str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", row["api_key_env"])
        ):
            raise ValueError(f"{model_id}: invalid api_key_env name")
        for key in ("auth_header", "auth_prefix"):
            value = row.get(key, getattr(ModelConfig, key))
            if not isinstance(value, str) or "\r" in value or "\n" in value:
                raise ValueError(f"{model_id}: invalid {key}")
        if not re.fullmatch(r"[A-Za-z0-9-]+", row.get("auth_header", "Authorization")):
            raise ValueError(f"{model_id}: invalid auth_header")
        timeout = row.get("timeout_seconds", 60)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0 < timeout <= 86400
            or not math.isfinite(timeout)
        ):
            raise ValueError(f"{model_id}: timeout_seconds must be positive and finite")
        if not isinstance(row.get("structured_output", "json_schema"), str) or row.get(
            "structured_output", "json_schema"
        ) not in {"json_schema", "json_object", "none"}:
            raise ValueError(f"{model_id}: invalid structured_output")
        models.append(ModelConfig(**row))
    if not models:
        raise ValueError("Configure at least one model")
    return models
