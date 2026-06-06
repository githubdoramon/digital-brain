from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("GOOGLE_CLIENT_IDS", "test-client-id")
os.environ.setdefault("ALLOWED_USERS", "user@example.com")

from routes import contacts


def test_contacts_raise_http_for_llm_unavailable_uses_503():
    with pytest.raises(HTTPException) as exc_info:
        contacts._raise_http_for_llm_unavailable(RuntimeError("boom"))

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "The LLM service is currently unavailable. Please try again shortly."
