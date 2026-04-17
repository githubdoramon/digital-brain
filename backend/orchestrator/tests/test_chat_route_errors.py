from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("LLM_BASE_URL", "http://localhost:11434/v1")
os.environ.setdefault("LLM_CHAT_MODEL_FAST", "test-fast")
os.environ.setdefault("LLM_CHAT_MODEL_SMART", "test-smart")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("ALLOWED_USERS", "user@example.com")

from routes import chat


def test_llm_unavailable_error_payload_uses_service_message():
    payload = chat._llm_unavailable_error_payload()

    assert payload == {
        "code": "llm_unavailable",
        "message": "The LLM service is currently unavailable. Please try again shortly.",
    }


def test_raise_http_for_llm_unavailable_uses_503():
    with pytest.raises(HTTPException) as exc_info:
        chat._raise_http_for_llm_unavailable(RuntimeError("boom"))

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "The LLM service is currently unavailable. Please try again shortly."
