from __future__ import annotations

from datetime import datetime, timedelta, timezone

import proposed_events


def _segment(minutes: int) -> proposed_events.StaySegment:
    start = datetime(2026, 6, 16, 9, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=minutes)
    return proposed_events.StaySegment(
        start_at=start,
        end_at=end,
        samples=[{"id": 1, "captured_at": start}],
        place_id=None,
        place_name="Roadside stop",
        city="Example City",
        country="Example Country",
        lat=1.0,
        lon=2.0,
        signature="name:roadside-stop",
    )


def test_timeline_segment_does_not_mark_short_stay_as_overlap(monkeypatch):
    monkeypatch.setattr(
        proposed_events,
        "_find_overlapping_events",
        lambda **_kwargs: [{"id": "event:other", "title": "Other event"}],
    )

    result = proposed_events._serialize_timeline_segment(
        _segment(minutes=3),
        ignores=set(),
        user_email="user@example.test",
    )

    assert result["skip_reason"] == "short_stay"
    assert result["overlaps_event"] is False
    assert result["overlapping_events"] == []


def test_timeline_segment_includes_overlap_evidence_for_candidate(monkeypatch):
    monkeypatch.setattr(
        proposed_events,
        "_find_overlapping_events",
        lambda **_kwargs: [{"id": "event:known", "title": "Known event"}],
    )
    monkeypatch.setattr(
        proposed_events,
        "_event_blocks_location_segment",
        lambda _segment, _event: {
            "blocks_proposal": True,
            "confidence": "high",
            "reason": "Same activity.",
        },
    )

    result = proposed_events._serialize_timeline_segment(
        _segment(minutes=45),
        ignores=set(),
        user_email="user@example.test",
    )

    assert result["skip_reason"] == "overlapping_event"
    assert result["overlaps_event"] is True
    assert result["overlapping_events"] == [
        {
            "id": "event:known",
            "title": "Known event",
            "overlap_decision": {
                "blocks_proposal": True,
                "confidence": "high",
                "reason": "Same activity.",
            },
        }
    ]
    assert result["would_propose"] is False


def test_timeline_segment_ignores_unrelated_overlapping_event(monkeypatch):
    monkeypatch.setattr(
        proposed_events,
        "_find_overlapping_events",
        lambda **_kwargs: [{"id": "event:broad-region", "title": "Example Region"}],
    )
    monkeypatch.setattr(
        proposed_events,
        "_event_blocks_location_segment",
        lambda _segment, _event: {
            "blocks_proposal": False,
            "confidence": "high",
            "reason": "The event is broad travel context, not the same stay.",
        },
    )

    result = proposed_events._serialize_timeline_segment(
        _segment(minutes=45),
        ignores=set(),
        user_email="user@example.test",
    )

    assert result["skip_reason"] == "eligible_candidate"
    assert result["overlaps_event"] is False
    assert result["overlapping_events"] == []
    assert result["would_propose"] is True


def test_same_place_event_blocks_without_llm(monkeypatch):
    segment = _segment(minutes=45)
    segment.place_id = "place:cafe-alpha"

    def fail_call(*_args, **_kwargs):
        raise AssertionError("LLM should not be called for exact place matches")

    monkeypatch.setattr(proposed_events, "call_llm_json", fail_call)

    decision = proposed_events._event_blocks_location_segment(
        segment,
        {"id": "event:lunch", "title": "Lunch", "place_id": "place:cafe-alpha"},
    )

    assert decision["blocks_proposal"] is True
    assert decision["confidence"] == "high"


def test_llm_can_disambiguate_unrelated_timed_overlap(monkeypatch):
    captured_prompts: list[str] = []

    def fake_call(prompt: str, **_kwargs):
        captured_prompts.append(prompt)
        return {
            "blocks_proposal": False,
            "confidence": "high",
            "reason": "Broad regional context does not describe the venue stay.",
        }

    monkeypatch.setattr(proposed_events, "call_llm_json", fake_call)

    decision = proposed_events._event_blocks_location_segment(
        _segment(minutes=45),
        {
            "id": "event:region",
            "title": "Example Region",
            "summary": "General trip context",
            "start_at": "2026-06-16T09:00:00+00:00",
            "end_at": "2026-06-16T18:00:00+00:00",
        },
    )

    assert decision == {
        "blocks_proposal": False,
        "confidence": "high",
        "reason": "Broad regional context does not describe the venue stay.",
    }
    assert captured_prompts


def test_full_day_events_do_not_block_location_gaps():
    start = datetime(2026, 6, 16, tzinfo=timezone.utc)

    assert (
        proposed_events._event_blocks_location_gap(
            {
                "id": "event:all-day",
                "start_date": start,
                "end_date": start + timedelta(hours=24),
                "raw": {},
            }
        )
        is False
    )


def test_timed_events_block_location_gaps():
    start = datetime(2026, 6, 16, 12, tzinfo=timezone.utc)

    assert (
        proposed_events._event_blocks_location_gap(
            {
                "id": "event:timed",
                "start_date": start,
                "end_date": start + timedelta(hours=1),
                "raw": {},
            }
        )
        is True
    )
