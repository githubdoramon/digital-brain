from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

import llm_helpers


@pytest.mark.parametrize("native_status", [404, 405])
def test_warmup_falls_back_to_configured_chat_api(monkeypatch, native_status):
    monkeypatch.setenv("LLM_BASE_URL", "https://models.example/v1")
    monkeypatch.setattr(llm_helpers, "is_ollama_base_url", lambda _: True)
    calls = []
    responses = iter(
        [
            MagicMock(status_code=native_status),
            MagicMock(status_code=200, json=lambda: {"choices": [{"message": {"content": "OK"}}]}),
        ]
    )

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return next(responses)

    monkeypatch.setattr(llm_helpers.requests, "post", post)
    assert llm_helpers.warm_chat_model("test-model", timeout=9)
    assert calls[1][0] == "https://models.example/v1/chat/completions"
    assert calls[1][1]["json"]["max_tokens"] == 1
    assert calls[1][1]["json"]["reasoning_effort"] == "none"
    assert calls[1][1]["json"]["reasoning"] == {"effort": "none"}
    assert "keep_alive" not in calls[1][1]["json"]


def test_warmup_fallback_preserves_provider_failures(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://models.example/v1")
    monkeypatch.setattr(llm_helpers, "is_ollama_base_url", lambda _: True)
    failed = MagicMock(status_code=500)
    failed.raise_for_status.side_effect = requests.HTTPError("upstream unavailable")
    responses = iter([MagicMock(status_code=404), failed])
    monkeypatch.setattr(llm_helpers.requests, "post", lambda *a, **k: next(responses))
    with pytest.raises(requests.HTTPError):
        llm_helpers.warm_chat_model("test-model")


def test_sensitive_json_request_redacts_input_output_and_reasoning(monkeypatch, caplog):
    monkeypatch.setenv("LLM_DIAGNOSTICS_ENABLED", "false")
    import logging

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": '{"answer":"PRIVATE_OUTPUT"}',
                    "reasoning_content": "PRIVATE_REASONING",
                }
            }
        ]
    }
    monkeypatch.setattr(llm_helpers.requests, "post", lambda *a, **k: response)
    caplog.set_level(logging.INFO)
    result = llm_helpers.call_llm_json("PRIVATE_INPUT", sensitive_payload=True)
    assert result == {"answer": "PRIVATE_OUTPUT"}
    assert "Sensitive LLM" in caplog.text
    assert all(
        secret not in caplog.text
        for secret in ("PRIVATE_INPUT", "PRIVATE_OUTPUT", "PRIVATE_REASONING")
    )


def test_build_chat_payload_merges_system_messages_at_the_start(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")

    messages = [
        {"role": "system", "content": "base instructions"},
        {"role": "user", "content": "hello"},
        {"role": "system", "content": "runtime context"},
        {"role": "assistant", "content": "previous answer"},
        {"role": "system", "content": "validation feedback"},
    ]

    payload = llm_helpers.build_chat_payload(messages, model="test-model")

    assert payload["messages"] == [
        {
            "role": "system",
            "content": "base instructions\n\nruntime context\n\nvalidation feedback",
        },
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "previous answer"},
    ]
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "system"


def test_build_chat_payload_attaches_keep_alive_for_configured_chat_models(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_CHAT_MODEL_FAST", "fast-model")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "smart-model")
    monkeypatch.setenv("OLLAMA_CHAT_KEEP_ALIVE", "-1")

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
    assert fast_payload["reasoning_effort"] == "none"
    assert smart_payload["model"] == "smart-model"
    assert smart_payload["keep_alive"] == -1
    assert smart_payload["reasoning_effort"] == "low"


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


def test_build_chat_payload_maps_legacy_high_reasoning_to_x_high(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "smart-model")

    payload = llm_helpers.build_chat_payload(
        [{"role": "user", "content": "hi"}],
        use_fast_model=False,
        reasoning_effort="high",
    )

    assert payload["reasoning_effort"] == "xhigh"
    assert payload["reasoning"] == {"effort": "xhigh"}


def test_warm_fast_model_uses_ollama_chat_endpoint(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_CHAT_MODEL_FAST", "fast-model")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "smart-model")
    monkeypatch.setenv("OLLAMA_CHAT_KEEP_ALIVE", "24h")

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
        "messages": [],
        "stream": False,
        "keep_alive": "24h",
    }
    assert kwargs["timeout"] == 12


async def test_warm_configured_chat_models_warms_fast_and_smart(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_CHAT_MODEL_FAST", "fast-model")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "smart-model")
    monkeypatch.setenv("OLLAMA_CHAT_KEEP_ALIVE", "24h")

    calls = []

    async def fake_warm_chat_model(payload, *, timeout):
        calls.append((payload["model"], timeout, payload["keep_alive"]))
        return {"done": True}

    monkeypatch.setattr(llm_helpers, "_post_chat_model_warmup_async", fake_warm_chat_model)

    warmed = await llm_helpers.warm_configured_chat_models(timeout=12)

    assert warmed == ["fast-model", "smart-model"]
    assert calls == [("fast-model", 12, "24h"), ("smart-model", 12, "24h")]


async def test_warm_configured_chat_models_dedupes_same_model(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_CHAT_MODEL_FAST", "same-model")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "same-model")

    calls = []

    async def fake_warm_chat_model(payload, *, timeout):
        calls.append(payload["model"])
        return {"done": True}

    monkeypatch.setattr(llm_helpers, "_post_chat_model_warmup_async", fake_warm_chat_model)

    warmed = await llm_helpers.warm_configured_chat_models()

    assert warmed == ["same-model"]
    assert calls == ["same-model"]


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
        "messages": [],
        "stream": False,
        "keep_alive": "20m",
    }
    assert kwargs["timeout"] == 9


def test_warm_chat_model_uses_configured_default_timeout(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(llm_helpers, "LLM_WARMUP_TIMEOUT", 180)

    response = MagicMock()
    response.json.return_value = {"done": True, "done_reason": "load"}
    response.raise_for_status = MagicMock()

    mock_post = MagicMock(return_value=response)
    monkeypatch.setattr(llm_helpers.requests, "post", mock_post)

    warmed = llm_helpers.warm_chat_model("large-model", keep_alive="20m")

    assert warmed is True
    _, kwargs = mock_post.call_args
    assert kwargs["timeout"] == 180


async def test_warm_configured_chat_models_continues_after_model_failure(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_CHAT_MODEL_FAST", "fast-model")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "smart-model")

    calls = []

    async def fake_warm_chat_model(payload, *, timeout):
        calls.append(payload["model"])
        if payload["model"] == "fast-model":
            raise requests.Timeout("timed out")
        return {"done": True}

    monkeypatch.setattr(llm_helpers, "_post_chat_model_warmup_async", fake_warm_chat_model)

    warmed = await llm_helpers.warm_configured_chat_models()

    assert warmed == ["smart-model"]
    assert calls == ["fast-model", "smart-model"]


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


def test_parse_llm_json_content_repairs_trailing_comma():
    parsed = llm_helpers.parse_llm_json_content(
        '{\n  "intent": "contact_lookup",\n  "confidence": 0.85,\n  "constraints": [],\n  "pre_resolve_contacts": true,\n  "reasoning": "test",\n}'
    )

    assert parsed["intent"] == "contact_lookup"
    assert parsed["pre_resolve_contacts"] is True


def test_parse_llm_json_content_extracts_balanced_object_from_extra_text():
    parsed = llm_helpers.parse_llm_json_content(
        'Here is the result:\n```json\n{"intent":"data_query","confidence":0.9,"constraints":[],"pre_resolve_contacts":false,"reasoning":"ok"}\n```\nThanks!'
    )

    assert parsed["intent"] == "data_query"


def test_call_llm_json_agentic_continues_reasoning_only_response(monkeypatch):
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning": "I have analyzed the transcript but have not emitted the object yet.",
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"summary":"Done","action_items":[]}',
                    }
                }
            ]
        },
    ]
    calls = []

    def fake_call_llm_chat(messages, **kwargs):
        calls.append((messages, kwargs))
        return responses.pop(0)

    monkeypatch.setattr(llm_helpers, "call_llm_chat", fake_call_llm_chat)

    result = llm_helpers.call_llm_json_agentic(
        "Summarize the transcript.",
        system_prompt="Return JSON.",
        reasoning_effort="xhigh",
        max_turns=3,
    )

    assert result == {"summary": "Done", "action_items": []}
    assert len(calls) == 2
    continuation_messages = calls[1][0]
    assert continuation_messages[-2] == {"role": "assistant", "content": ""}
    assert "prior_reasoning" in continuation_messages[-1]["content"]
    assert calls[0][1]["reasoning_effort"] == "xhigh"
