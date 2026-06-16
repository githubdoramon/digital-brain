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

    result = proposed_events._serialize_timeline_segment(
        _segment(minutes=45),
        ignores=set(),
        user_email="user@example.test",
    )

    assert result["skip_reason"] == "overlapping_event"
    assert result["overlaps_event"] is True
    assert result["overlapping_events"] == [{"id": "event:known", "title": "Known event"}]
    assert result["would_propose"] is False
