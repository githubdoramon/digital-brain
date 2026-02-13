import sys
from types import SimpleNamespace

from tools.handlers.memory import handle_get_events


def test_get_events_by_ids_uses_events_service(monkeypatch):
    fake_events_service = SimpleNamespace(
        get_events=lambda event_ids: [
            {
                "id": event_ids[0],
                "title": "Sample event",
                "start_date": "2026-02-10T10:00:00+00:00",
                "end_date": "2026-02-10T10:30:00+00:00",
                "people": ["contact:alice"],
            }
        ]
    )
    monkeypatch.setitem(sys.modules, "events", fake_events_service)

    result = handle_get_events({"action": "by_ids", "event_ids": ["event:1"]})

    assert result["count"] == 1
    assert result["events"][0]["id"] == "event:1"


def test_get_events_by_time_span_requires_bounds():
    result = handle_get_events({"action": "by_time_span", "time_start": "2026-02-01T00:00:00Z"})

    assert "error" in result
    assert "time_start and time_end" in result["error"]
