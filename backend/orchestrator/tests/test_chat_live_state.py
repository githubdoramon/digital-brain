import os
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("LLM_BASE_URL", "http://localhost:11434/v1")
os.environ.setdefault("LLM_CHAT_MODEL_FAST", "test-fast")
os.environ.setdefault("LLM_CHAT_MODEL_SMART", "test-smart")

from auth import get_current_user
from routes.chat import create_chat_router


def test_revision_endpoint_requires_auth_and_only_probes_the_authenticated_owner(monkeypatch):
    app = FastAPI()
    app.include_router(create_chat_router())
    probe = Mock(return_value={"thread_id": "thread:sample", "revision": "2026-10-30T12:00:00+00:00"})
    monkeypatch.setattr("conversations.get_chat_state", probe)
    client = TestClient(app)
    assert client.get("/mobile/chat-state").status_code == 401
    probe.assert_not_called()
    app.dependency_overrides[get_current_user] = lambda: {"email": "alex@example.com"}
    response = client.get("/mobile/chat-state?thread_id=thread:sample")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"thread_id", "revision"}
    probe.assert_called_once_with("alex@example.com", "thread:sample")
