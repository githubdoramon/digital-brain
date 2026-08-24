from datetime import datetime, timedelta, timezone

import pytest

from commands.handlers.event import (
    _can_use_current_location_for_event,
    _event_extraction_client_context,
    handle_event,
)
from commands.parser import ParsedCommand

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _location(*, captured_at: datetime = NOW) -> dict:
    return {
        "lat": 38.72,
        "lon": -9.13,
        "captured_at": captured_at.isoformat(),
    }


@pytest.mark.parametrize(
    ("start_offset", "expected"),
    [
        (timedelta(hours=-2), True),
        (timedelta(hours=-2, seconds=-1), False),
        (timedelta(minutes=30), True),
        (timedelta(minutes=30, seconds=1), False),
    ],
)
def test_current_location_uses_asymmetric_event_window(start_offset, expected):
    assert (
        _can_use_current_location_for_event(
            {"when": NOW + start_offset, "end_when": None},
            _location(),
            now=NOW,
        )
        is expected
    )


def test_current_location_is_allowed_when_event_interval_contains_now():
    assert _can_use_current_location_for_event(
        {
            "when": NOW - timedelta(hours=4),
            "end_when": NOW + timedelta(minutes=15),
        },
        _location(),
        now=NOW,
    )


def test_current_location_rejects_stale_capture():
    assert not _can_use_current_location_for_event(
        {"when": NOW, "end_when": None},
        _location(captured_at=NOW - timedelta(minutes=30, seconds=1)),
        now=NOW,
    )


def test_current_location_rejects_date_only_or_unknown_event_time():
    assert not _can_use_current_location_for_event(
        {"when": NOW, "when_granularity": "date"},
        _location(),
        now=NOW,
    )
    assert not _can_use_current_location_for_event(
        {"when": None},
        _location(),
        now=NOW,
    )


def test_event_extraction_context_does_not_expose_runtime_location():
    assert _event_extraction_client_context(
        {
            "client_context": {
                "timezone": "Europe/Lisbon",
                "locale": "en-PT",
                "location": _location(),
                "inferred_location": {"place_name": "Home"},
            }
        }
    ) == {"timezone": "Europe/Lisbon", "locale": "en-PT"}


def _patch_event_flow(monkeypatch, extracted):
    monkeypatch.setattr(
        "commands.handlers.event._extract_event_entities_with_llm",
        lambda *_args, **_kwargs: dict(extracted),
    )
    monkeypatch.setattr(
        "commands.handlers.event._resolve_contacts_with_agent",
        lambda *_args, **_kwargs: (
            {
                "contacts": [],
                "new_entities": {"contacts": [], "places": [], "documents": []},
                "name_replacements": {},
            },
            {"ambiguous_contacts": [], "suggested_relationships": []},
        ),
    )
    monkeypatch.setattr(
        "commands.handlers.event._find_event_matches",
        lambda *_args, **_kwargs: {"operation": "create", "candidates": []},
    )


def test_historical_event_does_not_infer_current_place(monkeypatch):
    runtime_now = datetime.now(timezone.utc)
    _patch_event_flow(
        monkeypatch,
        {
            "title": "Visited the exhibition",
            "summary": "Visited an exhibition earlier.",
            "when": runtime_now - timedelta(hours=2, seconds=1),
            "end_when": None,
            "where": None,
            "tags": [],
            "types": ["personal"],
            "need_user_input": None,
        },
    )
    monkeypatch.setattr(
        "commands.handlers.event.infer_current_place",
        lambda *_args, **_kwargs: pytest.fail("historical event must not infer current place"),
    )

    result = handle_event(
        ParsedCommand(
            command="event",
            args="visited an exhibition earlier",
            raw_message="/event visited an exhibition earlier",
        ),
        {
            "user_email": "user@example.com",
            "client_context": {"location": _location(captured_at=runtime_now)},
        },
    )

    assert result["type"] == "event_confirmation"
    assert result["extracted"]["where"] is None
    assert "inferred_location" not in result["resolution"]


def test_historical_prompt_location_is_preserved_without_current_proximity(monkeypatch):
    runtime_now = datetime.now(timezone.utc)
    _patch_event_flow(
        monkeypatch,
        {
            "title": "Visited Example Museum",
            "summary": "Visited Example Museum earlier.",
            "when": runtime_now - timedelta(hours=3),
            "end_when": None,
            "where": "Example Museum",
            "tags": [],
            "types": ["personal"],
            "need_user_input": None,
        },
    )
    observed_client_locations = []

    def fake_place_match(_where, *, client_location=None):
        observed_client_locations.append(client_location)
        return {
            "place_id": "plc_example_museum",
            "name": "Example Museum",
            "match_confidence": "high",
            "matched_via": "name",
            "match_score": 100,
        }

    monkeypatch.setattr(
        "commands.handlers.event.infer_current_place",
        lambda *_args, **_kwargs: pytest.fail("explicit historical place must not infer current place"),
    )
    monkeypatch.setattr(
        "commands.handlers.event.places_service.find_best_place_match",
        fake_place_match,
    )

    result = handle_event(
        ParsedCommand(
            command="event",
            args="visited Example Museum three hours ago",
            raw_message="/event visited Example Museum three hours ago",
        ),
        {
            "user_email": "user@example.com",
            "client_context": {"location": _location(captured_at=runtime_now)},
        },
    )

    assert result["extracted"]["where"] == "Example Museum"
    assert observed_client_locations == [None]
