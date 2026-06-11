import pytest
from fastapi import HTTPException

from commands.event import _normalize_event_modifications
from commands.event_datetime import event_timezone_from_context, parse_event_datetime
from commands.handlers import event as event_handler


def test_parse_event_datetime_uses_client_timezone_for_naive_local_time():
    event_tz = event_timezone_from_context(
        {"client_context": {"timezone": "Europe/Lisbon"}}
    )

    parsed = parse_event_datetime("2026-06-11T10:00:00", default_tz=event_tz)

    assert parsed.hour == 10
    assert parsed.utcoffset().total_seconds() == 3600
    assert parsed.isoformat() == "2026-06-11T10:00:00+01:00"


def test_event_extraction_defaults_naive_model_time_to_client_timezone(monkeypatch):
    def fake_call_llm_json(*_args, **_kwargs):
        return {
            "title": "Project check-in",
            "summary": "Discussed the roadmap.",
            "when": "2026-06-11T10:00:00",
            "end_when": None,
            "where": None,
            "documents": [],
            "tags": ["work"],
            "types": ["meeting"],
            "need_user_input": None,
        }

    monkeypatch.setattr("llm_helpers.call_llm_json", fake_call_llm_json)
    monkeypatch.setattr("tags_manager.MAJOR_TAGS", ["work", "personal"])
    monkeypatch.setattr("user_facts.get_hard_rules_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("user_facts.get_facts_for_context", lambda *_args, **_kwargs: None)

    result = event_handler._extract_event_entities_with_llm(
        "met at 10am",
        {"client_context": {"timezone": "Europe/Lisbon"}},
    )

    assert result["when"].isoformat() == "2026-06-11T10:00:00+01:00"


def test_event_modification_datetime_uses_command_timezone():
    event_tz = event_timezone_from_context(
        {"client_context": {"timezone": "Europe/Lisbon"}}
    )

    modifications = _normalize_event_modifications(
        {"when": "2026-06-11T10:00"},
        default_tz=event_tz,
    )

    assert modifications["when"].isoformat() == "2026-06-11T10:00:00+01:00"


def test_event_modification_rejects_invalid_datetime():
    with pytest.raises(HTTPException):
        _normalize_event_modifications({"when": "not-a-date"})
