from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import places
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


def test_enrichment_prompt_includes_place_context():
    candidate = {
        "start_at": datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc),
        "end_at": datetime(2026, 6, 16, 13, 0, tzinfo=timezone.utc),
        "duration_minutes": 60,
        "place_id": "place:cafe-alpha",
        "place_name": "Cafe Alpha",
        "city": "Example City",
        "country": "Example Country",
        "suggested_title": "Visited Cafe Alpha",
        "suggested_summary": "Stayed around 60 minutes.",
        "suggested_contact_ids": [],
    }
    context = {
        "place_context": {
            "known_place": True,
            "place": {"place_id": "place:cafe-alpha", "name": "Cafe Alpha"},
            "web_search": {
                "query": "Cafe Alpha Example City Example Country",
                "results": [{"title": "Cafe Alpha", "snippet": "A small cafe."}],
            },
        },
        "linked_contacts": [],
        "recent_events": [],
        "recurrence": {"same_place_event_count": 0},
    }

    prompt = json.loads(proposed_events._build_enrichment_prompt(candidate, context))

    assert prompt["task"]["goal"] == "Create a reviewable proposed event from passive location evidence."
    assert prompt["place_context"]["web_search"]["results"][0]["snippet"] == "A small cafe."
    assert "Use 'Visited <place>' only when the likely activity is unclear." in prompt["decision_guidance"]["title"]


def test_llm_enrichment_appends_known_place_description(monkeypatch):
    appended: dict[str, str] = {}

    def fake_append(place_id: str, note: str) -> bool:
        appended["place_id"] = place_id
        appended["note"] = note
        return True

    monkeypatch.setattr(proposed_events.places_service, "append_place_description_note", fake_append)
    candidate = {
        "place_id": "place:cafe-alpha",
        "suggested_title": "Visited Cafe Alpha",
        "suggested_summary": "Stayed around 60 minutes.",
        "reason": "Location stay.",
        "confidence": "medium",
        "suggested_contact_ids": [],
        "evidence": {},
    }
    context = {
        "place_context": {
            "known_place": True,
            "place": {"place_id": "place:cafe-alpha", "name": "Cafe Alpha", "description": "User note: good pastries."},
            "web_search": {"query": "Cafe Alpha", "results": [{"url": "https://example.test/cafe-alpha"}]},
        },
        "linked_contacts": [],
        "recent_events": [],
    }
    enriched = {
        "suggested_title": "Coffee at Cafe Alpha",
        "suggested_summary": "A 60 minute stay at Cafe Alpha around midday suggests a coffee or lunch stop.",
        "suggested_contact_ids": [],
        "confidence": "medium",
        "reason": "The stay duration and venue context support a cafe visit.",
        "recurrence_hint": None,
        "place_category": "cafe",
        "place_summary": (
            "Cafe Alpha is a public cafe in Example City, useful context for interpreting "
            "midday stays as coffee, snack, or lunch visits."
        ),
        "proposed_place_name": None,
    }

    result = proposed_events._apply_llm_enrichment(candidate, enriched, context, allowed_contact_ids=set())

    assert result["suggested_title"] == "Coffee at Cafe Alpha"
    assert appended["place_id"] == "place:cafe-alpha"
    assert "public cafe" in appended["note"]
    assert result["evidence"]["place_intelligence"]["description_appended"] is True


def test_web_place_evidence_can_trigger_enrichment_without_history(monkeypatch):
    captured_prompts: list[dict] = []
    segment = _segment(minutes=45)

    def fake_context(*_args, **_kwargs):
        return {
            "place_context": {
                "known_place": False,
                "place": {"name": "Cafe Alpha", "city": "Example City"},
                "web_search": {
                    "query": "Cafe Alpha Example City",
                    "results": [{"title": "Cafe Alpha", "snippet": "Neighborhood cafe and bakery."}],
                },
            },
            "linked_contacts": [],
            "recent_events": [],
            "recurrence": {"same_place_event_count": 0},
        }

    def fake_call(prompt: str, **_kwargs):
        captured_prompts.append(json.loads(prompt))
        return {
            "suggested_title": "Cafe stop at Cafe Alpha",
            "suggested_summary": "The stay lines up with a cafe visit based on venue context.",
            "suggested_contact_ids": [],
            "confidence": "medium",
            "reason": "The place context explains the likely activity without prior history.",
            "recurrence_hint": None,
            "place_category": "cafe",
            "place_summary": "Cafe Alpha appears to be a neighborhood cafe and bakery.",
            "proposed_place_name": "Cafe Alpha",
        }

    monkeypatch.setattr(proposed_events, "_build_history_context", fake_context)
    monkeypatch.setattr(proposed_events, "call_llm_json", fake_call)
    candidate = {
        "place_id": None,
        "place_name": "Cafe Alpha",
        "suggested_title": "Visited Cafe Alpha",
        "suggested_summary": "Stayed around 45 minutes.",
        "reason": "Location stay.",
        "confidence": "medium",
        "suggested_contact_ids": [],
        "evidence": {},
    }

    result = proposed_events._enrich_candidate_with_history(
        candidate,
        segment=segment,
        timezone_name="UTC",
    )

    assert result["suggested_title"] == "Cafe stop at Cafe Alpha"
    assert result["evidence"]["place_intelligence"]["proposed_place_name"] == "Cafe Alpha"
    assert captured_prompts[0]["place_context"]["web_search"]["results"][0]["snippet"] == "Neighborhood cafe and bakery."


def test_place_description_append_preserves_existing_text(monkeypatch):
    state = {"description": "User note: good pastries."}

    class FakeCursor:
        rowcount = 0

        def execute(self, query, params):
            if query.strip().startswith("SELECT"):
                self._row = {"description": state["description"]}
                return
            state["description"] = params[0]
            self.rowcount = 1

        def fetchone(self):
            return self._row

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(places, "get_conn", lambda: FakeConn())

    assert places.append_place_description_note("place:cafe-alpha", "Cafe Alpha is a neighborhood cafe.") is True
    assert state["description"] == "User note: good pastries.\n\nCafe Alpha is a neighborhood cafe."
    assert places.append_place_description_note("place:cafe-alpha", "Cafe Alpha is a neighborhood cafe.") is False
