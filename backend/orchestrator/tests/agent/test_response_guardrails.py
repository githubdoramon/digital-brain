"""Tests for response-output guardrail helpers."""

from agent.response_guardrails import (
    looks_like_code_describing_tool,
    looks_like_continuation,
    looks_like_malformed_tool_call,
)


def test_detects_continuation_intent_text():
    assert looks_like_continuation("Let me search for that in your memories.")


def test_continuation_guard_ignores_long_content():
    assert not looks_like_continuation("a" * 1001)


def test_detects_malformed_json_tool_call_output():
    content = '{"type":"function","name":"search_memories","arguments":{"query":"test"}}'
    assert looks_like_malformed_tool_call(content)


def test_detects_code_describing_tool_usage():
    content = "action = 'call_tool'\\ntool_name = 'HassTurnOff'\\narguments = {'name': 'office lights'}"
    assert looks_like_code_describing_tool(content)


def test_non_tool_plain_text_not_flagged():
    content = "I found your most recent meeting and here is the summary."
    assert not looks_like_malformed_tool_call(content)
    assert not looks_like_code_describing_tool(content)
