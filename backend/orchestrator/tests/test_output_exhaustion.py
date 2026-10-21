# ruff: noqa: F811
"""Synthetic provider exhaustion must never turn partial output into saved facts."""

import json
from unittest.mock import MagicMock

import pytest

import intelligence as store
import intelligence_context
import intelligence_model as model
import llm_helpers
import llm_policy
from tests.test_intelligence import (  # noqa: F401
    FUTURE,
    OWNER,
    database,
    finding,
    ingest,
    setup_owner,
)


@pytest.mark.parametrize("content", ['{"findings": []}', '{"findings": []} {"broken":'])
def test_json_rejects_length_before_even_valid_or_salvageable_json(monkeypatch, content):
    monkeypatch.setattr(
        llm_helpers,
        "_call_llm_raw",
        lambda *a, **k: {
            "choices": [{"finish_reason": "length", "message": {"content": content}}],
            "usage": {"completion_tokens": 20000},
        },
    )
    parser = MagicMock()
    monkeypatch.setattr(llm_helpers, "parse_llm_json_content", parser)
    with pytest.raises(llm_helpers.LLMOutputExhaustedError):
        llm_helpers.call_llm_json("synthetic evidence")
    parser.assert_not_called()
    assert llm_policy.failure_code(llm_helpers.LLMOutputExhaustedError()) == "output_exhausted"
    # Plain text callers can intentionally request a short, capped response.
    assert llm_helpers.call_llm("short answer") == content


def test_stream_collector_preserves_exhaustion_and_empty_usage_choices(monkeypatch):
    response = MagicMock(status_code=200)
    response.iter_lines.return_value = [
        "data: " + json.dumps({"choices": [{"delta": {"content": '{"ok":true}'}}]}),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "length"}]}),
        "data: " + json.dumps({"choices": [], "usage": {"completion_tokens": 20000}}),
        "data: [DONE]",
    ]
    monkeypatch.setattr(llm_helpers.requests, "post", lambda *a, **k: response)
    result = llm_helpers._post_chat_completion_stream({"model": "synthetic", "messages": []})
    assert result["choices"][0]["finish_reason"] == "length"
    assert result["usage"]["completion_tokens"] == 20000
    response.close.assert_called_once()


def test_default_learning_budget_reserves_more_output_and_respects_provider_cap(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.setenv("KNOWLEDGE_CONTEXT_WINDOW_TOKENS", "65536")
    monkeypatch.setenv("KNOWLEDGE_CONTEXT_MARGIN_TOKENS", "4096")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW_TOKENS", "65536")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "0")
    llm_policy._CONSTRAINTS.clear()
    assert intelligence_context.request_limits() == (41440, 20000)
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "16000")
    budget, output = intelligence_context.request_limits()
    assert output == 16000
    assert budget + output + 4096 <= 65536


def test_exhaustion_retry_changes_only_failed_request_and_is_bounded(monkeypatch):
    calls = []

    def ask(prompt, **kwargs):
        calls.append((json.loads(prompt), kwargs))
        if len(calls) == 1:
            raise llm_helpers.LLMOutputExhaustedError()
        return {"findings": []}

    monkeypatch.setattr(model, "call_llm_json", ask)
    payload = {
        "sources": [{"ref": "1:0", "text_spans": ["A synthetic observation."]}],
        "primary_refs": ["1:0"],
        "context": {},
    }
    assert model._ask(payload, phase="Extract supported candidates")["findings"] == []
    assert len(calls) == 2
    assert calls[0][0]["sources"] == calls[1][0]["sources"]
    assert "exactly ONE compact JSON object" in calls[1][1]["system_prompt"]
    assert calls[0][1]["reasoning_effort"] == calls[1][1]["reasoning_effort"] == "medium"
    calls.clear()

    def always_exhaust(*a, **k):
        calls.append(1)
        raise llm_helpers.LLMOutputExhaustedError()

    monkeypatch.setattr(model, "call_llm_json", always_exhaust)
    with pytest.raises(llm_helpers.LLMOutputExhaustedError):
        model._ask(payload, phase="Extract supported candidates")
    assert len(calls) == 2


def test_reconciliation_exhaustion_splits_without_reextracting(database, monkeypatch):
    setup_owner(database)
    ingest(database)
    batch = store.next_batch(OWNER, FUTURE)
    candidates = [{**finding(batch[0]), "predicate": f"observation_{i}"} for i in range(2)]
    calls = []

    def ask(prompt, **kwargs):
        payload = json.loads(prompt)
        calls.append(payload)
        if "candidates" not in payload:
            return {"findings": candidates}
        if len(payload["candidates"]) > 1:
            raise llm_helpers.LLMOutputExhaustedError()
        return {"findings": payload["candidates"]}

    monkeypatch.setattr(model, "call_llm_json", ask)
    _, findings = model.analyze(OWNER, batch, knowledge_only=True)
    assert len(findings) == 2
    assert sum("candidates" not in call for call in calls) == 1
    assert [len(call["candidates"]) for call in calls if "candidates" in call] == [2, 2, 1, 1]


def test_extraction_exhaustion_splits_primary_sources_without_losing_coverage(
    database, monkeypatch
):
    setup_owner(database)
    for i in range(3):
        ingest(database, ident=f"source-{i}", body=f"A synthetic observation number {i}.")
    batch = store.next_batch(OWNER, FUTURE)
    succeeded = []

    def ask(prompt, **kwargs):
        payload = json.loads(prompt)
        if len(payload["primary_refs"]) > 1:
            raise llm_helpers.LLMOutputExhaustedError()
        succeeded.extend(payload["primary_refs"])
        return {"findings": []}

    monkeypatch.setattr(model, "call_llm_json", ask)
    _, findings = model.analyze(OWNER, batch, knowledge_only=True)
    assert not findings
    assert sorted(succeeded) == sorted(row["ref"] for row in batch)


def test_terminal_reconciliation_exhaustion_never_restarts_extraction(database, monkeypatch):
    setup_owner(database)
    for i in range(3):
        ingest(database, ident=f"source-{i}")
    batch = store.next_batch(OWNER, FUTURE)
    calls = []

    def ask(prompt, **kwargs):
        payload = json.loads(prompt)
        calls.append(payload)
        if "candidates" not in payload:
            return {"findings": [finding(batch[0])]}
        raise llm_helpers.LLMOutputExhaustedError()

    monkeypatch.setattr(model, "call_llm_json", ask)
    with pytest.raises(llm_helpers.LLMOutputExhaustedError):
        model.analyze(OWNER, batch, knowledge_only=True)
    assert len(calls) == 3
    assert sum("candidates" not in call for call in calls) == 1
