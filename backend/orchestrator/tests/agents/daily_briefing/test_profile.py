"""Tests for daily briefing bounded profile wiring."""

from agents.daily_briefing.profile import (
    DAILY_BRIEFING_ALLOWED_TOOLS,
    EVENT_RESEARCH_ALLOWED_TOOLS,
    build_daily_briefing_agent_profile,
    build_daily_briefing_tools_and_handlers,
    build_event_research_profile,
    build_event_research_tools_and_handlers,
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


# -- Per-event research profile tests ----------------------------------------


def test_event_research_profile_identity():
    profile = build_event_research_profile()
    assert profile.name == "daily_briefing_event_research"
    assert profile.runtime.name == "daily_briefing_event_research"


def test_event_research_profile_has_tight_limits():
    profile = build_event_research_profile()
    assert profile.runtime.max_steps <= 3
    assert profile.runtime.max_tool_calls <= 4
    assert profile.runtime.timeout_seconds <= 60


def test_event_research_tool_policy_contains_web_tools():
    tools, handlers = build_event_research_tools_and_handlers()
    tool_names = {tool.get("function", {}).get("name") for tool in tools}
    assert set(EVENT_RESEARCH_ALLOWED_TOOLS).issubset(tool_names)
    assert set(EVENT_RESEARCH_ALLOWED_TOOLS).issubset(set(handlers.keys()))


def test_event_research_profile_exposes_hooks():
    profile = build_event_research_profile()
    assert profile.build_tools_and_handlers is not None
    assert profile.get_system_prompt is not None
    system_prompt = profile.get_system_prompt()
    assert "research" in system_prompt.lower()
