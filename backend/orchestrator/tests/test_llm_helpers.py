from __future__ import annotations

from unittest.mock import MagicMock

import requests

import llm_helpers


def test_build_chat_payload_attaches_keep_alive_only_for_fast_model(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_CHAT_MODEL_FAST", "fast-model")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "smart-model")
    monkeypatch.setenv("OLLAMA_FAST_KEEP_ALIVE", "-1")

    fast_payload = llm_helpers.build_chat_payload(
        [{"role": "user", "content": "hi"}],
        use_fast_model=True,
    )
    smart_payload = llm_helpers.build_chat_payload(
        [{"role": "user", "content": "hi"}],
        use_fast_model=False,
    )

    assert fast_payload["model"] == "fast-model"
    assert fast_payload["keep_alive"] == -1
    assert smart_payload["model"] == "smart-model"
    assert "keep_alive" not in smart_payload


def test_build_chat_payload_uses_keep_alive_override_for_any_model(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_CHAT_MODEL_FAST", "fast-model")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "smart-model")

    with llm_helpers.use_llm_keep_alive("180s"):
        payload = llm_helpers.build_chat_payload(
            [{"role": "user", "content": "hi"}],
            model="custom-model",
        )

    assert payload["model"] == "custom-model"
    assert payload["keep_alive"] == "180s"


def test_warm_fast_model_uses_ollama_chat_endpoint(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_CHAT_MODEL_FAST", "fast-model")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "smart-model")
    monkeypatch.setenv("OLLAMA_FAST_KEEP_ALIVE", "24h")

    response = MagicMock()
    response.json.return_value = {"done": True, "done_reason": "load"}
    response.raise_for_status = MagicMock()

    mock_post = MagicMock(return_value=response)
    monkeypatch.setattr(llm_helpers.requests, "post", mock_post)

    warmed = llm_helpers.warm_fast_model(timeout=12)

    assert warmed is True
    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert mock_post.call_args.args[0] == "http://localhost:11434/api/chat"
    assert kwargs["json"] == {
        "model": "fast-model",
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
        "keep_alive": "24h",
    }
    assert kwargs["timeout"] == 12


def test_warm_chat_model_uses_requested_model(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")

    response = MagicMock()
    response.json.return_value = {"done": True, "done_reason": "load"}
    response.raise_for_status = MagicMock()

    mock_post = MagicMock(return_value=response)
    monkeypatch.setattr(llm_helpers.requests, "post", mock_post)

    warmed = llm_helpers.warm_chat_model("qwen3.5:0.8b", timeout=9, keep_alive="20m")

    assert warmed is True
    _, kwargs = mock_post.call_args
    assert kwargs["json"] == {
        "model": "qwen3.5:0.8b",
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
        "keep_alive": "20m",
    }
    assert kwargs["timeout"] == 9


def test_is_llm_unavailable_error_recognizes_retryable_http_errors():
    response = MagicMock()
    response.status_code = 503
    exc = requests.HTTPError("HTTP 503", response=response)

    assert llm_helpers.is_llm_unavailable_error(exc) is True


def test_post_chat_completion_wraps_timeout_as_llm_unavailable(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(llm_helpers, "LLM_MAX_RETRIES", 1)
    monkeypatch.setattr(
        llm_helpers.requests,
        "post",
        MagicMock(side_effect=requests.Timeout("timed out")),
    )

    try:
        llm_helpers._post_chat_completion(
            {"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
            timeout=1,
        )
    except llm_helpers.LLMUnavailableError as exc:
        assert str(exc) == "LLM service is unavailable"
    else:
        raise AssertionError("Expected LLMUnavailableError")
