"""Regression cases for rejected requests, private diagnostics and durable recovery."""

import json
import logging
import threading
from unittest.mock import MagicMock

import httpx
import pytest
import requests

import intelligence_model as model
import llm_helpers
import llm_policy as policy
from job_leases import lease_heartbeat


@pytest.fixture(autouse=True)
def isolated_provider(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://models.example/v1")
    monkeypatch.setenv("LLM_CHAT_MODEL_FAST", "fast-test")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "smart-test")
    monkeypatch.setenv("LLM_TIMEOUT", "180")
    monkeypatch.setenv("LLM_REASONING_TIMEOUT", "300")
    monkeypatch.setenv("LLM_HIGH_REASONING_TIMEOUT", "900")
    monkeypatch.setattr(policy, "_CONSTRAINTS", {})


def response(status, body):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()
    return result


@pytest.mark.parametrize(
    "effort,explicit,expected",
    [
        ("none", 10, 10),
        ("low", None, 180),
        ("medium", 90, 300),
        ("high", 90, 900),
        ("xhigh", 1200, 1200),
    ],
)
def test_reasoning_timeouts(effort, explicit, expected):
    assert policy.request_timeout({"reasoning_effort": effort}, explicit) == expected


def test_sensitive_400_is_classified_without_logging_provider_text(monkeypatch, caplog):
    post = MagicMock(
        return_value=response(
            400,
            {
                "error": {
                    "message": "PRIVATE_SOURCE maximum input is 8192 tokens",
                    "param": "PRIVATE_PARAM",
                }
            },
        )
    )
    monkeypatch.setattr(llm_helpers.requests, "post", post)
    caplog.set_level(logging.INFO)
    with pytest.raises(policy.LLMRequestRejected, match=r"^context_length_exceeded$"):
        llm_helpers._post_chat_completion(
            {"model": "smart-test", "messages": []}, sensitive_payload=True
        )
    assert post.call_count == 1
    assert "reason=context_length_exceeded" in caplog.text
    assert "PRIVATE" not in caplog.text


def test_output_limit_is_adapted_once_and_remembered_per_provider(monkeypatch):
    replies = iter(
        [
            response(
                400,
                {
                    "error": {
                        "message": "Output must be between 0 and 4096 tokens, including reasoning tokens."
                    }
                },
            ),
            response(200, {"choices": [{"message": {"content": '{"ok":true}'}}]}),
        ]
    )
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return next(replies)

    monkeypatch.setattr(llm_helpers.requests, "post", post)
    assert llm_helpers.call_llm_json(
        "A synthetic task.", max_tokens=12000, sensitive_payload=True
    ) == {"ok": True}
    assert [p["max_tokens"] for p in calls] == [12000, 4096]
    assert (
        llm_helpers.build_chat_payload([], model="fast-test", max_tokens=12000)["max_tokens"]
        == 4096
    )
    monkeypatch.setenv("LLM_BASE_URL", "https://other-models.example/v1")
    assert (
        llm_helpers.build_chat_payload([], model="fast-test", max_tokens=12000)["max_tokens"]
        == 12000
    )


def test_unsupported_schema_uses_json_without_unbounded_retry(monkeypatch):
    calls = []

    def call(*args, **kwargs):
        calls.append(kwargs["response_format"])
        raise policy.LLMRequestRejected("unsupported_response_format")

    monkeypatch.setattr(llm_helpers, "call_llm", call)
    with pytest.raises(policy.LLMRequestRejected):
        llm_helpers.call_llm_json("A synthetic task.", response_format={"type": "json_schema"})
    assert calls == [{"type": "json_schema"}, {"type": "json_object"}]


def test_unknown_400_is_not_blindly_retried(monkeypatch):
    call = MagicMock(side_effect=policy.LLMRequestRejected("invalid_request"))
    monkeypatch.setattr(llm_helpers, "call_llm", call)
    with pytest.raises(policy.LLMRequestRejected):
        llm_helpers.call_llm_json("A synthetic task.")
    assert call.call_count == 1


def test_strict_schema_preserves_properties_named_default_and_literal_values():
    schema = {
        "type": "object",
        "properties": {
            "default": {"type": "string", "default": "x"},
            "literal": {"const": {"default": "keep"}},
        },
    }
    strict = llm_helpers.build_json_schema_response_format(name="test", schema=schema)[
        "json_schema"
    ]["schema"]
    assert strict["properties"]["default"] == {"type": "string"}
    assert strict["properties"]["literal"]["const"] == {"default": "keep"}


def test_lease_heartbeat_stops_after_ownership_is_lost():
    ready = threading.Event()
    threads = []

    def renew():
        threads.append(threading.current_thread())
        ready.set()
        return False

    with lease_heartbeat(renew, interval=0.001):
        assert ready.wait(2)
    assert len(threads) == 1 and not threads[0].is_alive()


def test_strict_schema_requires_optional_properties_without_mutating_model_schema():
    schema = {"type": "object", "properties": {"optional": {"type": "string", "default": ""}}}
    strict = llm_helpers.build_json_schema_response_format(name="test", schema=schema)[
        "json_schema"
    ]["schema"]
    assert strict["required"] == ["optional"]
    assert strict["additionalProperties"] is False
    assert "default" not in strict["properties"]["optional"]
    assert schema["properties"]["optional"]["default"] == ""


def test_request_budget_includes_schema_instructions_and_all_source_text():
    payload = {
        "sources": [{"ref": "1:0", "text": "An intact source."}],
        "context": {"knowledge": [{"statement": "Old context " * 100}]},
    }
    size = len(model._json({**payload, "context": {"knowledge": []}})) + 100
    fitted = model._fit_payload(payload, size, "instructions", {"schema": {}})
    assert fitted["sources"] == payload["sources"]
    assert payload["context"]["knowledge"]
    assert len(model._json(fitted)) + len("instructions") + len(model._json({"schema": {}})) <= size
    with pytest.raises(policy.LLMRequestRejected):
        model._fit_payload(payload, 10, "instructions", {})


def test_context_rejection_splits_without_dropping_parts_or_downgrading_history(monkeypatch):
    seen = []

    def analyze(owner, batch, *, knowledge_only):
        assert owner == "reader@example.com" and knowledge_only
        if len(batch) > 1:
            raise policy.LLMRequestRejected("context_length_exceeded")
        seen.append(batch[0]["ref"])
        return {"deferred_refs": [batch[0]["ref"]]}, [{"statement": batch[0]["ref"]}]

    monkeypatch.setattr(model, "_analyze_repaired", analyze)
    refs = [f"{n}:0" for n in range(5)]
    context, findings = model.analyze(
        "reader@example.com", [{"ref": ref} for ref in refs], knowledge_only=True
    )
    assert seen == refs == context["deferred_refs"]
    assert [f["statement"] for f in findings] == refs


def test_spans_preserve_every_character_including_short_final_fragment():
    text = "An anonymous source.\n" * 100 + "x"
    spans = model.source_spans(text)
    assert "".join(spans) == text
    assert all(8 <= len(span) <= 1200 for span in spans)


@pytest.mark.parametrize(
    "evidence",
    [
        {"ref": "unknown", "span_index": 0},
        {"ref": "1:0", "span_index": 99},
        {"ref": "1:0", "span_index": 0, "quote": "A different source."},
    ],
)
def test_controller_rejects_invalid_span_citations(evidence):
    with pytest.raises(model.InvalidModelOutput, match="invalid_evidence"):
        model.expand_citations(
            {"findings": [{"evidence": [evidence]}]},
            [{"ref": "1:0", "text": "An anonymous source."}],
        )


async def test_async_stream_uses_reasoning_timeout(monkeypatch):
    real_client = httpx.AsyncClient
    seen = []

    def client(**kwargs):
        seen.append(kwargs["timeout"].read)
        return real_client(
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text="data: [DONE]\n\n")
            ),
        )

    monkeypatch.setattr(llm_helpers.httpx, "AsyncClient", client)
    assert [
        line
        async for line in llm_helpers.stream_llm_chat(
            [], model="smart-test", reasoning_effort="high", timeout=90
        )
    ]
    assert seen == [900]


def test_sync_stream_uses_reasoning_timeout(monkeypatch):
    result = MagicMock(status_code=200)
    result.iter_lines.return_value = ["data: [DONE]"]
    post = MagicMock(return_value=result)
    monkeypatch.setattr(llm_helpers.requests, "post", post)
    llm_helpers._post_chat_completion_stream(
        {"model": "smart-test", "messages": [], "reasoning_effort": "medium"}, timeout=90
    )
    assert post.call_args.kwargs["timeout"] == 300
