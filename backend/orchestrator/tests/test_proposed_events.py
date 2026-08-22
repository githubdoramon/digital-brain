from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import places
import proposed_events


def test_daily_scan_dates_returns_current_and_recent_local_dates():
    assert proposed_events.daily_scan_dates(date(2026, 8, 22)) == [
        date(2026, 8, 22),
        date(2026, 8, 21),
    ]
    assert proposed_events.daily_scan_dates(date(2026, 8, 22), lookback_days=3) == [
        date(2026, 8, 22),
        date(2026, 8, 21),
        date(2026, 8, 20),
    ]


def test_daily_scan_cutoff_does_not_shift_lisbon_scan_to_next_date(monkeypatch):
    monkeypatch.setattr(proposed_events, "_latest_timezone", lambda _email: "Europe/Lisbon")

    before_cutoff = proposed_events.should_run_daily_scan(
        "user@example.test",
        now_utc=datetime(2026, 8, 22, 3, 59, tzinfo=timezone.utc),
    )
    at_cutoff = proposed_events.should_run_daily_scan(
        "user@example.test",
        now_utc=datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc),
    )

    assert before_cutoff is None
    assert at_cutoff == {
        "target_date": date(2026, 8, 22),
        "timezone": "Europe/Lisbon",
        "dedupe_key": "proposed-events:2026-08-22:Europe/Lisbon",
    }


def test_analyze_user_window_aggregates_recent_daily_runs(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        proposed_events,
        "_latest_timezone",
        lambda _email: "Europe/Lisbon",
    )
    monkeypatch.setattr(proposed_events, "expire_pending", lambda **_kwargs: 0)
    monkeypatch.setattr(
        proposed_events,
        "_fetch_locations",
        lambda **kwargs: captured.update(fetch=kwargs) or [],
    )
    monkeypatch.setattr(proposed_events, "_fetch_ignores", lambda _email: set())
    monkeypatch.setattr(
        proposed_events,
        "_build_stay_segments",
        lambda rows, *, day_end: captured.update(day_end=day_end) or [],
    )
    monkeypatch.setattr(
        proposed_events,
        "_analyze_segments",
        lambda **kwargs: {
            "created": 1,
            "skipped": 2,
            "skip_reasons": {"short_stay": 2},
            "proposal_count": 1,
            "proposals": [{"proposal_id": "proposal:window"}],
            "location_count": len(kwargs["rows"]),
            "segment_count": len(kwargs["segments"]),
        },
    )

    result = proposed_events.analyze_user_window(
        user_email="user@example.test",
        target_date=date(2026, 8, 22),
        timezone_name="Europe/Lisbon",
        lookback_days=2,
    )

    assert result["created"] == 1
    assert result["skipped"] == 2
    assert result["skip_reasons"] == {"short_stay": 2}
    assert result["scan_start_date"] == "2026-08-21"
    assert result["scan_end_date"] == "2026-08-22"
    assert result["scanned_dates"] == ["2026-08-22", "2026-08-21"]
    assert result["location_count"] == 0
    assert result["segment_count"] == 0
    assert captured["fetch"]["start_at"].isoformat() == "2026-08-20T23:00:00+00:00"
    assert captured["fetch"]["end_at"].isoformat() == "2026-08-22T23:00:00+00:00"


def test_stay_segments_group_nearby_points_without_following_a_long_chain():
    start = datetime(2026, 6, 16, 9, 0, tzinfo=timezone.utc)
    rows = [
        {"id": 1, "lat": 38.70000, "lon": -9.10000, "captured_at": start, "place_name": "Cafe Alpha"},
        {
            "id": 2,
            "lat": 38.70020,
            "lon": -9.10000,
            "captured_at": start + timedelta(minutes=10),
            "place_name": "Cafe Alpha patio",
        },
        {
            "id": 3,
            "lat": 38.70170,
            "lon": -9.10000,
            "captured_at": start + timedelta(minutes=20),
            "place_name": "Example School",
        },
    ]

    segments = proposed_events._build_stay_segments(
        rows,
        day_end=start + timedelta(hours=1),
    )

    assert len(segments) == 2
    assert len(segments[0].samples) == 2
    assert len(segments[1].samples) == 1


def test_uncertain_internal_place_match_does_not_bypass_google(monkeypatch):
    monkeypatch.setattr(
        proposed_events,
        "_nearest_known_place",
        lambda *_args: {
            "place_id": "plc_existing",
            "name": "Existing Place",
            "city": "Example City",
            "country": "Example Country",
            "distance_m": 40.0,
            "confidence": "medium",
        },
    )

    result = proposed_events._enrich_location(
        {
            "lat": 38.7,
            "lon": -9.1,
            "accuracy_m": 60,
            "place_name": "Old label",
        }
    )

    assert result["place_id"] is None
    assert result["known_place_match"]["place_id"] == "plc_existing"
    assert result["known_place_match"]["confidence"] == "medium"


def test_selected_google_place_is_materialized_only_at_acceptance(monkeypatch):
    created: dict[str, object] = {}
    candidate = {
        "provider_place_id": "ChIJexample",
        "title": "Example Cafe",
        "lat": 38.7,
        "lon": -9.1,
        "formatted_address": "1 Example Street",
        "city": "Example City",
        "country": "Example Country",
    }
    monkeypatch.setattr(proposed_events.google_place_cache, "get_canonical_place_id", lambda _id: None)
    monkeypatch.setattr(proposed_events.google_place_cache, "get_candidate", lambda _id: candidate)
    monkeypatch.setattr(
        proposed_events.places_service,
        "ingest_place",
        lambda place: created.update(place=place),
    )
    monkeypatch.setattr(
        proposed_events.google_place_cache,
        "link_canonical_place",
        lambda provider_id, place_id: created.update(provider_id=provider_id, place_id=place_id),
    )

    place_id = proposed_events._materialize_selected_place(
        {
            "place_id": None,
            "evidence": {"place_candidates": [candidate]},
        },
        "ChIJexample",
    )

    assert place_id and place_id.startswith("plc_example-cafe_")
    assert created["provider_id"] == "ChIJexample"
    assert created["place"].name == "Example Cafe"


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


def test_duration_humanization_and_overnight_context():
    assert proposed_events._humanize_duration_minutes(75) == "1 hour and 15 minutes"
    assert proposed_events._humanize_duration_minutes(66) == "a bit more than 1 hour"
    assert (
        proposed_events._normalize_generated_event_text("You stayed 75 minutes at Cafe Alpha.", duration_minutes=75)
        == "You stayed 1 hour and 15 minutes at Cafe Alpha."
    )

    start = datetime(2026, 6, 16, 22, 30, tzinfo=timezone.utc)
    end = datetime(2026, 6, 17, 7, 15, tzinfo=timezone.utc)
    context = proposed_events._build_time_context(start, end, "UTC")

    assert context["likely_overnight_sleep"] is True
    assert "overnight stay or sleep" in context["interpretation_hint"]

    clipped_start = datetime(2026, 6, 16, 0, 4, tzinfo=timezone.utc)
    clipped_end = datetime(2026, 6, 16, 9, 42, tzinfo=timezone.utc)
    clipped_context = proposed_events._build_time_context(clipped_start, clipped_end, "UTC")

    assert clipped_context["spans_midnight"] is False
    assert clipped_context["day_window_clipped_overnight"] is True
    assert clipped_context["likely_overnight_sleep"] is True


def test_long_afternoon_to_morning_stay_splits_activity_and_sleep():
    start = datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc)
    segment = proposed_events.StaySegment(
        start_at=start,
        end_at=end,
        samples=[
            {"id": 1, "captured_at": start},
            {"id": 2, "captured_at": datetime(2026, 6, 16, 22, 30, tzinfo=timezone.utc)},
            {"id": 3, "captured_at": end},
        ],
        place_id=None,
        place_name="Example Guesthouse",
        city="Example City",
        country="Example Country",
        lat=1.0,
        lon=2.0,
        signature="name:example-guesthouse",
    )

    parts = proposed_events._proposal_candidate_segments(segment, timezone_name="UTC")

    assert len(parts) == 2
    assert parts[0].start_at == start
    assert parts[0].end_at == datetime(2026, 6, 16, 22, 0, tzinfo=timezone.utc)
    assert parts[1].start_at == datetime(2026, 6, 16, 22, 0, tzinfo=timezone.utc)
    assert parts[1].end_at == end
    assert proposed_events._build_time_context(parts[0].start_at, parts[0].end_at, "UTC")["likely_overnight_sleep"] is False
    assert proposed_events._build_time_context(parts[1].start_at, parts[1].end_at, "UTC")["likely_overnight_sleep"] is True


def test_short_pre_sleep_arrival_only_creates_sleep_candidate():
    start = datetime(2026, 6, 16, 21, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc)
    segment = proposed_events.StaySegment(
        start_at=start,
        end_at=end,
        samples=[
            {"id": 1, "captured_at": start},
            {"id": 2, "captured_at": datetime(2026, 6, 16, 22, 30, tzinfo=timezone.utc)},
            {"id": 3, "captured_at": end},
        ],
        place_id=None,
        place_name="Example Guesthouse",
        city="Example City",
        country="Example Country",
        lat=1.0,
        lon=2.0,
        signature="name:example-guesthouse",
    )

    parts = proposed_events._proposal_candidate_segments(segment, timezone_name="UTC")

    assert len(parts) == 1
    assert parts[0].start_at == datetime(2026, 6, 16, 22, 0, tzinfo=timezone.utc)
    assert parts[0].end_at == end
    assert proposed_events._build_time_context(parts[0].start_at, parts[0].end_at, "UTC")["likely_overnight_sleep"] is True


def test_enrichment_prompt_includes_place_context():
    candidate = {
        "start_at": datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc),
        "end_at": datetime(2026, 6, 16, 13, 0, tzinfo=timezone.utc),
        "duration_minutes": 60,
        "evidence": {
            "duration_label": "1 hour",
            "time_context": {"likely_overnight_sleep": False},
        },
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
    assert prompt["event_candidate"]["duration_label"] == "1 hour"
    assert prompt["place_context"]["web_search"]["results"][0]["snippet"] == "A small cafe."
    assert "Use 'Visited <place>' only when the likely activity is unclear." in prompt["decision_guidance"]["title"]
    assert "Write what likely happened, not why you believe it happened." in prompt["decision_guidance"]["summary"]
    assert "Keep reasoning out of suggested_summary so the user can edit the event notes without editing diagnostic text." in prompt["decision_guidance"]["reason"]


def test_serialized_proposal_includes_duration_label():
    result = proposed_events._serialize_proposal(
        {
            "proposal_id": "proposal:1",
            "duration_minutes": 75,
            "start_at": datetime(2026, 6, 16, 12, tzinfo=timezone.utc),
            "end_at": datetime(2026, 6, 16, 13, 15, tzinfo=timezone.utc),
        }
    )

    assert result["duration_label"] == "1 hour and 15 minutes"


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
                "place_candidates": [
                    {
                        "provider_place_id": "google:cafe-alpha",
                        "title": "Cafe Alpha",
                        "primary_type": "cafe",
                        "distance_m": 18.0,
                    }
                ],
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
            "selected_place_candidate_id": "google:cafe-alpha",
            "ranked_place_candidate_ids": ["google:cafe-alpha"],
            "place_confidence": "high",
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
    assert result["evidence"]["place_intelligence"]["proposed_place_name"] is None
    assert result["evidence"]["llm_enrichment"]["selected_place_candidate_id"] == "google:cafe-alpha"
    assert result["evidence"]["place_intelligence"]["candidates"][0]["title"] == "Cafe Alpha"
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
