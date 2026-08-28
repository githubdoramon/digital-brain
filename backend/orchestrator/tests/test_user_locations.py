from datetime import datetime, timedelta, timezone

import user_locations


class _FakeCursor:
    def __init__(self, row):
        self.row = row
        self.params = None

    def execute(self, _query, params):
        self.params = params

    def fetchone(self):
        return self.row


class _FakeConnection:
    def __init__(self, row):
        self.cursor_instance = _FakeCursor(row)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_instance


def test_should_not_skip_when_no_existing_location():
    should_skip = user_locations._should_skip_location_update(
        existing=None,
        captured_at=datetime.now(timezone.utc),
    )

    assert should_skip is False


def test_get_nearest_location_preserves_sample_provenance(monkeypatch):
    capture_at = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
    sample_at = capture_at - timedelta(seconds=45)
    connection = _FakeConnection(
        {
            "lat": 38.7,
            "lon": -9.1,
            "accuracy_m": 12.0,
            "captured_at": sample_at,
            "source": "expo_location",
        }
    )
    monkeypatch.setattr(user_locations, "get_conn", lambda: connection)

    result = user_locations.get_nearest_location(
        user_email="person@example.test",
        captured_at=capture_at,
    )

    assert result is not None
    assert result["source"] == "phone_location_history"
    assert result["sample_source"] == "expo_location"
    assert result["sample_captured_at"] == sample_at.isoformat()
    assert result["offset_ms"] == 45_000
    assert result["tolerance_ms"] == 600_000


def test_should_skip_when_update_is_stale():
    current = datetime.now(timezone.utc)
    existing = {
        "lat": 38.7222,
        "lon": -9.1393,
        "captured_at": current,
    }

    should_skip = user_locations._should_skip_location_update(
        existing=existing,
        captured_at=current - timedelta(seconds=5),
    )

    assert should_skip is True


def test_should_not_skip_newer_small_movement_within_throttle_window():
    current = datetime.now(timezone.utc)
    existing = {
        "lat": 38.7222,
        "lon": -9.1393,
        "captured_at": current,
    }

    should_skip = user_locations._should_skip_location_update(
        existing=existing,
        captured_at=current + timedelta(seconds=60),
    )

    assert should_skip is False


def test_should_not_skip_when_significant_movement_detected():
    current = datetime.now(timezone.utc)
    existing = {
        "lat": 38.7222,
        "lon": -9.1393,
        "captured_at": current,
    }

    should_skip = user_locations._should_skip_location_update(
        existing=existing,
        captured_at=current + timedelta(seconds=30),
    )

    assert should_skip is False


def test_should_not_skip_newer_small_movement_even_after_long_delay():
    current = datetime.now(timezone.utc)
    existing = {
        "lat": 38.7222,
        "lon": -9.1393,
        "captured_at": current,
    }

    should_skip = user_locations._should_skip_location_update(
        existing=existing,
        captured_at=current + timedelta(minutes=10),
    )

    assert should_skip is False
