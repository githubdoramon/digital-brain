from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

import llm_context as context
import llm_helpers
import llm_policy


@pytest.fixture(autouse=True)
def budgets(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://models.example/v1")
    monkeypatch.setenv("LLM_CHAT_MODEL_FAST", "fast-test")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "smart-test")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW_TOKENS", "16000")
    monkeypatch.setenv("LLM_CONTEXT_MARGIN_TOKENS", "1000")
    monkeypatch.setenv("LLM_DEFAULT_MAX_OUTPUT_TOKENS", "2000")
    monkeypatch.setenv("LLM_REASONING_MAX_OUTPUT_TOKENS", "4000")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "0")
    llm_policy._CONSTRAINTS.clear()
    yield
    llm_policy._CONSTRAINTS.clear()


def payload(messages, **kwargs):
    return {"model": "smart-test", "messages": messages, **kwargs}


def old_dialogue():
    return [
        {"role": "system", "content": "Controller instructions stay exact."},
        {"role": "user", "content": "Alex Carter chose the Slate laptop. " * 700},
        {"role": "assistant", "content": "We still need to confirm its warranty. " * 500},
        {"role": "user", "content": "What is still unresolved?"},
    ]


def result_messages(name="get_document", result=None):
    if result is None:
        result = {
            "document": {
                "document_id": "document-123",
                "title": "Warranty",
                "content": "Original source text. " * 2000,
            }
        }
    return [
        {"role": "user", "content": "Find the warranty."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-123",
                    "type": "function",
                    "function": {"name": name, "arguments": '{"id":"document-123"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-123", "content": json.dumps(result)},
    ]


def test_budget_accounts_for_reasoning_model_overrides_and_provider_limits(monkeypatch):
    assert context.request_budget("smart-test").input_limit == 13000
    assert context.request_budget("smart-test", reasoning_effort="xhigh").input_limit == 11000
    monkeypatch.setenv("LLM_CONTEXT_WINDOW_FAST_TOKENS", "10000")
    assert context.request_budget("fast-test").input_limit == 7000
    llm_policy.remember_constraint(
        "smart-test",
        llm_policy.LLMRequestRejected(
            "context_length_exceeded", context_limit=12000, input_limit=9000
        ),
    )
    assert context.request_budget("smart-test").input_limit == 8000
    llm_policy.remember_constraint("smart-test", input_budget=6500)
    assert context.request_budget("smart-test").input_limit == 6500


def test_complete_envelope_is_checked_before_any_outbound_call(monkeypatch):
    post = MagicMock()
    monkeypatch.setattr(llm_helpers.requests, "post", post)
    with pytest.raises(llm_policy.LLMRequestRejected, match="context_length_exceeded"):
        llm_helpers.call_llm_chat(
            [{"role": "user", "content": "Small question"}],
            tools=[
                {"type": "function", "function": {"name": "oversized", "description": "x" * 16000}}
            ],
        )
    post.assert_not_called()


@pytest.mark.parametrize("field", ["messages", "response_format", "tools"])
def test_estimate_includes_non_ascii_and_all_large_prompt_sections(field):
    value = {field: "😀" * 4000}
    assert context.estimate_input(value) > 16000
    with pytest.raises(llm_policy.LLMRequestRejected):
        context.prepare_request(
            payload([], **value) if field != "messages" else payload(value[field]),
            summarize=MagicMock(),
        )


def test_images_keep_original_payload_and_use_image_reservation(monkeypatch):
    monkeypatch.setenv("LLM_IMAGE_INPUT_TOKENS", "3000")
    original = payload(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + "A" * 80000},
                    },
                ],
            }
        ]
    )
    assert 3000 < context.estimate_input(original) < 4000
    fitted = context.prepare_request(original, summarize=MagicMock())
    assert fitted["messages"] == original["messages"]


def test_older_dialogue_compacts_in_bounded_calls_and_reuses_run_cache():
    original = payload(old_dialogue())
    snapshot = deepcopy(original)
    policy = context.CompactionPolicy()
    calls = []

    def summarize(request):
        calls.append(request)
        assert (
            context.estimate_input(request)
            <= context.request_budget("smart-test", output=512).input_limit
        )
        assert "tools" not in request
        assert request["reasoning_effort"] == "none"
        return "User chose a Slate laptop; its warranty remains unconfirmed."

    fitted = context.prepare_request(original, summarize=summarize, policy=policy)
    assert context.estimate_input(fitted) <= context.request_budget("smart-test").input_limit
    assert fitted["messages"][0] == original["messages"][0]
    assert fitted["messages"][-1] == original["messages"][-1]
    assert "Derived working summary" in fitted["messages"][1]["content"]
    assert original == snapshot
    call_count = len(calls)
    assert call_count > 1
    again = context.prepare_request(original, summarize=summarize, policy=policy)
    assert again == fitted
    assert len(calls) == call_count
    context.prepare_request(original, summarize=summarize, policy=context.CompactionPolicy())
    assert len(calls) > call_count


def test_all_current_request_messages_survive_controller_followups():
    messages = old_dialogue()
    question = messages[-1]["content"]
    messages += [
        {"role": "assistant", "content": "I will look it up."},
        {"role": "user", "content": "Controller: inspect the source before answering."},
    ]
    fitted = context.prepare_request(
        payload(messages),
        summarize=lambda _: "Warranty unconfirmed.",
        policy=context.CompactionPolicy(current_request=question),
    )
    assert fitted["messages"][-3:] == messages[-3:]


def test_read_only_tool_summary_preserves_pairing_exact_ids_and_source_state():
    messages = result_messages(
        result={
            "results": [
                {
                    "id": "document-123",
                    "source_ref": "source:123",
                    "status": "current",
                    "deleted": False,
                    "updated_at": "2026-01-02",
                    "content": "Evidence. " * 5000,
                }
            ],
        }
    )
    fitted = context.prepare_request(
        payload(messages),
        summarize=lambda _: "Warranty information.",
        policy=context.CompactionPolicy(read_only_tools=frozenset({"get_document"})),
    )
    assert fitted["messages"][:2] == messages[:2]
    assert fitted["messages"][2]["tool_call_id"] == "call-123"
    summary = json.loads(fitted["messages"][2]["content"])
    refs = {item["path"]: item["value"] for item in summary["references"]}
    assert refs["/results/0/id"] == "document-123"
    assert refs["/results/0/source_ref"] == "source:123"
    assert refs["/results/0/status"] == "current"
    assert refs["/results/0/deleted"] is False
    assert refs["/results/0/updated_at"] == "2026-01-02"
    assert context.estimate_input(fitted) < context.request_budget("smart-test").input_limit


@pytest.mark.parametrize(
    "tool,result",
    [
        ("write_record", {"success": True, "content": "Receipt. " * 5000}),
        ("get_document", {"error": "Repair needed.", "content": "Evidence. " * 5000}),
        ("get_document", {"success": False, "content": "Evidence. " * 5000}),
        ("get_document", {"valid": False, "content": "Evidence. " * 5000}),
        (
            "get_document",
            {"_validation": {"warnings": ["Check source"]}, "content": "Evidence. " * 5000},
        ),
    ],
)
def test_mutation_receipts_and_validation_failures_are_never_summarized(tool, result):
    summarize = MagicMock()
    with pytest.raises(llm_policy.LLMRequestRejected):
        context.prepare_request(
            payload(result_messages(tool, result)),
            summarize=summarize,
            policy=context.CompactionPolicy(read_only_tools=frozenset({"get_document"})),
        )
    summarize.assert_not_called()


@pytest.mark.parametrize("response", ["", "x" * 9000, None, RuntimeError("private provider body")])
def test_bad_compaction_cannot_send_partial_or_overflowing_context(response, caplog):
    original = payload(old_dialogue())
    snapshot = deepcopy(original)
    calls = []

    def summarize(request):
        calls.append(request)
        if isinstance(response, Exception):
            raise response
        return response

    with pytest.raises(llm_policy.LLMRequestRejected):
        context.prepare_request(original, summarize=summarize, policy=context.CompactionPolicy())
    assert len(calls) == 1
    assert original == snapshot
    assert "private provider body" not in caplog.text


def test_compaction_failure_keeps_usable_original_when_it_still_fits():
    original = payload(
        [
            {"role": "user", "content": "Older context. " * 770},
            {"role": "user", "content": "Continue."},
        ]
    )
    assert 10400 < context.estimate_input(original) < 13000
    fitted = context.prepare_request(
        original, summarize=lambda _: "", policy=context.CompactionPolicy()
    )
    assert fitted["messages"] == original["messages"]


def test_background_evidence_is_preserved_for_caller_owned_splitting(monkeypatch):
    post = MagicMock()
    monkeypatch.setattr(llm_helpers.requests, "post", post)
    with pytest.raises(llm_policy.LLMRequestRejected):
        llm_helpers.call_llm_json("Original source. " * 2000, sensitive_payload=True)
    post.assert_not_called()


def test_compactor_calls_are_sensitive_and_main_request_is_rechecked(monkeypatch):
    calls = []

    def post(request, **kwargs):
        calls.append((request, kwargs))
        content = "Warranty unconfirmed." if kwargs.get("sensitive_payload") else "Final answer."
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(llm_helpers, "_post_chat_completion", post)
    result = llm_helpers.call_llm_chat(old_dialogue())
    assert result["choices"][0]["message"]["content"] == "Final answer."
    assert len(calls) > 1
    assert all(kwargs["sensitive_payload"] for _, kwargs in calls[:-1])
    assert all(
        context.estimate_input(request)
        <= context.request_budget(request["model"], output=request["max_tokens"]).input_limit
        for request, _ in calls
    )


async def test_async_stream_compacts_without_blocking_the_event_loop(monkeypatch):
    import threading

    summarizing = threading.Event()
    release = threading.Event()
    sent = []

    def post(request, **kwargs):
        summarizing.set()
        assert release.wait(timeout=5)
        return {"choices": [{"message": {"content": "Warranty unconfirmed."}}]}

    class Response:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"Ready"},"finish_reason":"stop"}]}'

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            sent.append(kwargs["json"])
            return Response()

    monkeypatch.setattr(llm_helpers, "_post_chat_completion", post)
    monkeypatch.setattr(llm_helpers.httpx, "AsyncClient", Client)

    async def consume():
        return [line async for line in llm_helpers.stream_llm_chat(old_dialogue())]

    task = asyncio.create_task(consume())
    try:
        assert await asyncio.to_thread(summarizing.wait, 5)
        # If the synchronous compactor held the loop, this coroutine could not run.
        release.set()
        assert "Ready" in (await task)[0]
    finally:
        release.set()
        if not task.done():
            task.cancel()
    assert len(sent) == 1
    assert context.estimate_input(sent[0]) < context.request_budget("fast-test").input_limit
