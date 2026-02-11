"""Tests for daily briefing bounded profile wiring."""

from agents.daily_briefing.profile import (
    DAILY_BRIEFING_ALLOWED_TOOLS,
    build_daily_briefing_agent_profile,
    build_daily_briefing_tools_and_handlers,
    get_daily_briefing_profile,
)


def test_daily_briefing_profile_identity():
    profile = get_daily_briefing_profile()
    assert profile.name == "daily_briefing"


def test_daily_briefing_tool_policy_contains_allowed_tools():
    tools, handlers = build_daily_briefing_tools_and_handlers()
    tool_names = {tool.get("function", {}).get("name") for tool in tools}
    assert set(DAILY_BRIEFING_ALLOWED_TOOLS).issubset(tool_names)
    assert set(DAILY_BRIEFING_ALLOWED_TOOLS).issubset(set(handlers.keys()))


def test_daily_briefing_agent_profile_exposes_generic_hooks():
    profile = build_daily_briefing_agent_profile()
    assert profile.name == "daily_briefing"
    assert profile.runtime.name == "daily_briefing"
    assert profile.build_tools_and_handlers is not None
    assert profile.get_system_prompt is not None
