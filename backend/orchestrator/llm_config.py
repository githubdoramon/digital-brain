from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").strip()
LLM_CHAT_MODEL_FAST = os.getenv("LLM_CHAT_MODEL_FAST", "").strip()
LLM_CHAT_MODEL_SMART = os.getenv("LLM_CHAT_MODEL_SMART", "").strip()
OLLAMA_FAST_KEEP_ALIVE = os.getenv("OLLAMA_FAST_KEEP_ALIVE", "-1").strip()


def get_fast_model() -> str:
    model = os.getenv("LLM_CHAT_MODEL_FAST", LLM_CHAT_MODEL_FAST).strip()
    if not model:
        raise RuntimeError("LLM_CHAT_MODEL_FAST environment variable is required")
    return model


def get_smart_model() -> str:
    model = os.getenv("LLM_CHAT_MODEL_SMART", LLM_CHAT_MODEL_SMART).strip()
    if not model:
        raise RuntimeError("LLM_CHAT_MODEL_SMART environment variable is required")
    return model


def get_fast_keep_alive() -> str | int:
    value = os.getenv("OLLAMA_FAST_KEEP_ALIVE", OLLAMA_FAST_KEEP_ALIVE).strip() or "-1"
    if value.lstrip("-").isdigit():
        return int(value)
    return value


def resolve_chat_model(*, model: str | None = None, use_fast_model: bool | None = None) -> str:
    if model:
        return str(model).strip()
    if use_fast_model is False:
        return get_smart_model()
    return get_fast_model()


def is_ollama_base_url(base_url: str | None = None) -> bool:
    candidate = str(base_url or os.getenv("LLM_BASE_URL", LLM_BASE_URL)).strip()
    if not candidate:
        return False
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        return False
    path = parsed.path.rstrip("/")
    return path in {"", "/v1"}


def get_ollama_api_base_url(base_url: str | None = None) -> str:
    candidate = str(base_url or os.getenv("LLM_BASE_URL", LLM_BASE_URL)).strip()
    if not candidate:
        raise RuntimeError("LLM_BASE_URL environment variable is required")
    parsed = urlparse(candidate)
    path = parsed.path.rstrip("/")
    if path == "/v1":
        parsed = parsed._replace(path="")
    return urlunparse(parsed).rstrip("/")
