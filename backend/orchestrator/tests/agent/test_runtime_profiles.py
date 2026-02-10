"""Tests for bounded runtime profile definitions."""

from agent.runtime_profiles import build_daily_briefing_runtime_profile
from agents.main.profile import build_main_runtime_profile


def test_build_main_runtime_profile_uses_controller_limits():
    profile = build_main_runtime_profile(
        max_steps=15,
        max_tool_calls=20,
        timeout_seconds=120,
    )
    assert profile.name == "main"
    assert profile.max_steps == 15
    assert profile.max_tool_calls == 20
    assert profile.timeout_seconds == 120


def test_build_daily_briefing_runtime_profile_defaults():
    profile = build_daily_briefing_runtime_profile()
    assert profile.name == "daily_briefing"
    assert profile.max_steps == 8
    assert profile.max_tool_calls == 12
    assert profile.timeout_seconds == 180
    assert profile.temperature == 0.1
