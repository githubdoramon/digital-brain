"""Tests for dependency-aware batching in tool execution coordinator."""

import os

os.environ.setdefault("LLM_BASE_URL", "http://localhost:11434")
os.environ.setdefault("LLM_CHAT_MODEL", "test-model")

from agent.tool_executor import ToolExecutionCoordinator


class _DummyController:
    pass


def _call(tool_name: str) -> dict:
    return {
        "id": f"call-{tool_name}",
        "function": {
            "name": tool_name,
            "arguments": "{}",
        },
    }


def test_build_execution_batches_groups_parallel_safe_tools():
    coordinator = ToolExecutionCoordinator(_DummyController())
    batches = coordinator._build_execution_batches(
        [_call("search_memories"), _call("get_document"), _call("home_assistant")]
    )

    assert len(batches) == 2
    assert batches[0][0] is True
    assert len(batches[0][1]) == 2
    assert batches[1][0] is False
    assert batches[1][1][0]["function"]["name"] == "home_assistant"
