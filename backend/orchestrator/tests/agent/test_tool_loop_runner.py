"""Tests for shared profiled tool loop runner."""

from agent.runtime_profiles import BoundedRuntimeProfile
from agent.tool_loop_runner import run_profiled_tool_loop


def test_run_profiled_tool_loop_passes_profile_limits(monkeypatch):
    captured = {}

    def fake_call_llm_with_tools(
        prompt,
        *,
        tools,
        tool_handlers,
        system_prompt,
        use_fast_model,
        timeout,
        temperature,
        top_p,
        max_steps,
        max_tool_calls,
    ):
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        captured["use_fast_model"] = use_fast_model
        captured["timeout"] = timeout
        captured["temperature"] = temperature
        captured["top_p"] = top_p
        captured["max_steps"] = max_steps
        captured["max_tool_calls"] = max_tool_calls
        return {"content": "ok"}

    monkeypatch.setattr("agent.tool_loop_runner.call_llm_with_tools", fake_call_llm_with_tools)

    profile = BoundedRuntimeProfile(
        name="test",
        max_steps=3,
        max_tool_calls=5,
        timeout_seconds=42,
        temperature=0.2,
        top_p=0.9,
    )
    result = run_profiled_tool_loop(
        prompt="do task",
        system_prompt="rules",
        tools=[],
        tool_handlers={},
        profile=profile,
    )

    assert result == {"content": "ok"}
    assert captured["use_fast_model"] is False
    assert captured["timeout"] == 42
    assert captured["max_steps"] == 3
    assert captured["max_tool_calls"] == 5
