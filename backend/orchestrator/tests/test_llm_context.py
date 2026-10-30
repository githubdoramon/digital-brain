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
        {"role": "user", "content": "Alex Carter chose the Slate laptop. " * 1100},
        {"role": "assistant", "content": "We still need to confirm its warranty. " * 800},
        {"role": "user", "content": "What is still unresolved?"},
    ]


def test_conversational_soft_budget_compacts_before_hard_limit(monkeypatch):
    monkeypatch.setenv("LLM_CONTEXT_WINDOW_TOKENS", "64000")
    messages = old_dialogue()
    original = deepcopy(messages)
    request = payload(messages)
    assert context.estimate_input(request) < context.request_budget("smart-test").input_limit
    summaries = []

    def summarize(fragment):
        summaries.append(fragment)
        return "Alex Carter chose Slate; warranty confirmation remains unresolved."

    result = context.prepare_request(
        request, summarize=summarize,
        policy=context.CompactionPolicy(current_request="What is still unresolved?",
                                        target_input_tokens=2000),
    )
    assert summaries
    assert context.estimate_input(result) <= 2000
    assert result["messages"][-1] == messages[-1]
    assert result["messages"][0] == messages[0]
    assert messages == original


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
                {
                    "type": "function",
                    "function": {
                        "name": "oversized",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "choice": {
                                    "type": "string",
                                    "enum": [f"item-{i}" for i in range(10000)],
                                }
                            },
                        },
                    },
                }
            ],
        )
    post.assert_not_called()


@pytest.mark.parametrize("field", ["messages", "response_format", "tools"])
def test_estimate_includes_non_ascii_and_all_large_prompt_sections(field):
    content = "😀" * 10000
    value = {
        field: [{"role": "user", "content": content}]
        if field == "messages"
        else [{"type": "function", "function": {"name": "test", "parameters": {"enum": [content]}}}]
        if field == "tools"
        else {"json_schema": {"enum": [content]}}
    }
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
                    "content": "Evidence. " * 9000,
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
        ("write_record", {"success": True, "content": "Receipt. " * 9000}),
        ("get_document", {"error": "Repair needed.", "content": "Evidence. " * 9000}),
        ("get_document", {"success": False, "content": "Evidence. " * 9000}),
        ("get_document", {"valid": False, "content": "Evidence. " * 9000}),
        (
            "get_document",
            {"_validation": {"warnings": ["Check source"]}, "content": "Evidence. " * 9000},
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
            {"role": "user", "content": "Older context. " * 3100},
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
        llm_helpers.call_llm_json("Original source. " * 8000, sensitive_payload=True)
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


def test_initial_chat_with_large_instructions_uses_tokens_without_rewriting_them(monkeypatch):
    monkeypatch.setenv("LLM_CONTEXT_WINDOW_TOKENS", "65536")
    instructions = "Use tools to inspect sources and preserve the user's decisions. " * 1100
    messages = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": "Find my recent notes."},
    ]
    post = MagicMock(return_value={"choices": [{"message": {"content": "Ready."}}]})
    monkeypatch.setattr(llm_helpers, "_post_chat_completion", post)
    result = llm_helpers.call_llm_chat(messages, model="smart-test")
    post.assert_called_once()
    sent = post.call_args.args[0]
    measurement = context.input_measurement(sent)
    assert measurement["wire_bytes"] > 60000
    assert measurement["estimated_tokens"] < 30000
    assert measurement["estimator"] == "cl100k_base_with_20_percent_headroom"
    assert sent["messages"][0]["content"].startswith("Trusted time context:")
    assert sent["messages"][0]["content"].endswith(instructions)
    assert sent["messages"][1:] == messages[1:]
    assert result["choices"][0]["message"]["content"] == "Ready."


def test_large_tool_descriptions_shrink_without_changing_parameter_contracts():
    tool = {
        "type": "function",
        "function": {
            "name": "inspect_records",
            "description": "Read records with care. " * 5000,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind"],
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["event", "document"],
                        "description": "Choose a source kind. " * 1000,
                    }
                },
            },
        },
    }
    original = payload(
        [
            {"role": "system", "content": "Preserve these instructions."},
            {"role": "user", "content": "Inspect my records."},
        ],
        tools=[tool],
    )
    snapshot = deepcopy(original)
    summarize = MagicMock()
    assert context.estimate_input(original) > context.request_budget("smart-test").input_limit
    fitted = context.prepare_request(
        original, summarize=summarize, policy=context.CompactionPolicy()
    )
    assert fitted["messages"] == original["messages"]
    function = fitted["tools"][0]["function"]
    assert function["name"] == tool["function"]["name"]
    assert function["parameters"]["required"] == ["kind"]
    assert function["parameters"]["additionalProperties"] is False
    assert function["parameters"]["properties"]["kind"]["enum"] == ["event", "document"]
    assert original == snapshot
    summarize.assert_not_called()
    assert context.estimate_input(fitted) < context.request_budget("smart-test").input_limit


def test_missing_tokenizer_has_an_explicit_safe_fallback(monkeypatch):
    monkeypatch.setattr(context, "_tokenizer", lambda: None)
    request = payload([{"role": "user", "content": "😀" * 10000}])
    assert context.input_measurement(request)["estimator"] == "utf8_upper_bound"
    with pytest.raises(llm_policy.LLMRequestRejected):
        context.prepare_request(request, summarize=MagicMock())


def test_tool_compaction_preserves_literal_description_fields_in_schema_values():
    literal = {"description": "A literal value that must remain exact. " * 40}
    schema = {
        "const": literal,
        "default": literal,
        "enum": [literal],
        "examples": [literal],
        "description": "Actual schema annotation. " * 100,
    }
    compacted = context._compact_tool_descriptions(
        [{"type": "function", "function": {"name": "inspect_record", "parameters": schema}}]
    )[0]["function"]["parameters"]
    for key in ("const", "default", "enum", "examples"):
        assert compacted[key] == schema[key]
    assert len(compacted["description"]) == 240


def test_compaction_preserves_recent_corrections_and_originals():
    recent = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"Recent turn {i}"}
        for i in range(12)
    ]
    recent[-2]["content"] = "Ignore Dana Lewis. Continue the original Acme recap."
    question = {"role": "user", "content": "Answer my original question."}
    original = payload([*old_dialogue()[:-1], *recent, question])
    snapshot = deepcopy(original)
    requests = []

    def summarize(request):
        requests.append(request)
        return "User requested the Acme recap with Alex Carter; it remains unanswered."

    fitted = context.prepare_request(
        original,
        summarize=summarize,
        policy=context.CompactionPolicy(
            current_request=question["content"], recent_dialogue_messages=12
        ),
    )
    assert requests
    assert fitted["messages"][-13:] == [*recent, question]
    assert original == snapshot
    assert context.estimate_input(fitted) <= context.request_budget("smart-test").input_limit
    assert "original unanswered request" in requests[0]["messages"][0]["content"]
    assert "dismissed people/topics" in requests[0]["messages"][0]["content"]


def test_repeated_current_question_does_not_pin_the_whole_history():
    question = {"role": "user", "content": "Answer the original question."}
    messages = [question, *old_dialogue()[1:-1], question]
    units = context._candidates(
        messages, context.CompactionPolicy(current_request=question["content"])
    )
    assert units == [(0, len(messages) - 1, None)]


@pytest.mark.parametrize(
    "validation",
    [
        {"suggested_tools": ["get_events"]},
        {"status": "satisfied", "reason": "Read completed"},
        {"status": "needs_more_tools", "suggested_tools": ["web_search"]},
    ],
)
def test_successful_read_feedback_can_compact_without_losing_validation(validation):
    from agent.llm_transport import _compaction_policy
    from tools.registry import get_registry

    registry = get_registry()
    policy = _compaction_policy(registry.get_tool_definitions_for_groups(["memory"]))
    assert {"search_memories", "get_message", "get_conversation"} <= policy.read_only_tools
    fact = {
        "id": "knowledge:one",
        "kind": "knowledge",
        "subject_ref": "contact:alex",
        "subjects": ["contact:alex", "entity:beacon"],
        "status": "uncertain",
        "inferred": True,
        "valid_until": "2026-12-31",
        "evidence": [{"source_id": "source:one", "quote": "Original evidence. " * 7000}],
    }
    original = payload(
        result_messages(
            "search_memories", {"results": [fact], "count": 1, "_validation": validation}
        )
    )
    snapshot = deepcopy(original)
    assert context.estimate_input(original) > context.request_budget("smart-test").input_limit
    fitted = context.prepare_request(
        original,
        summarize=lambda _: "Alex may work on Beacon; source evidence is uncertain.",
        policy=policy,
    )
    assert context.estimate_input(fitted) <= context.request_budget("smart-test").input_limit
    compact = json.loads(fitted["messages"][2]["content"])
    refs = {item["path"]: item["value"] for item in compact["references"]}
    assert refs["/_validation"] == validation
    for key in ("subject_ref", "subjects", "status", "inferred", "valid_until"):
        assert refs["/results/0/" + key] == fact[key]
    assert refs["/results/0/evidence/0/source_id"] == "source:one"
    assert fitted["messages"][2]["tool_call_id"] == "call-123"
    assert original == snapshot


@pytest.mark.parametrize(
    "validation",
    [
        {"status": "failed"},
        {"status": "need_user_input"},
        {"warnings": ["Check source"]},
        {"status": ["invalid-shape"]},
    ],
)
def test_real_validation_failures_remain_protected(validation):
    assert context.has_protected_tool_feedback({"results": [], "_validation": validation})


def test_event_compaction_keeps_exact_attendee_identities_for_public_research():
    people = [
        {
            "contact_id": "contact:alex",
            "display_name": "Alex Carter",
            "emails": ["alex@example.com"],
        },
        {
            "contact_id": "contact:dana",
            "display_name": "Dana Lewis",
            "emails": ["dana@example.org"],
        },
    ]
    result = {
        "events": [
            {
                "id": "event:review",
                "title": "Beacon review",
                "people": people,
                "summary": "Meeting notes. " * 9000,
            }
        ],
        "count": 1,
    }
    fitted = context.prepare_request(
        payload(result_messages("get_events", result)),
        summarize=lambda _: "Beacon review discussed release timing.",
        policy=context.CompactionPolicy(read_only_tools=frozenset({"get_events"})),
    )
    compact = json.loads(fitted["messages"][2]["content"])
    refs = {item["path"]: item["value"] for item in compact["references"]}
    for i, person in enumerate(people):
        for key, value in person.items():
            assert refs[f"/events/0/people/{i}/{key}"] == value
