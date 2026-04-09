from datetime import datetime, timedelta, timezone

import user_locations


def test_should_not_skip_when_no_existing_location():
    should_skip = user_locations._should_skip_location_update(
        existing=None,
        lat=38.7223,
        lon=-9.1394,
        captured_at=datetime.now(timezone.utc),
    )

    assert should_skip is False


def test_should_skip_when_update_is_stale():
    current = datetime.now(timezone.utc)
    existing = {
        "lat": 38.7222,
        "lon": -9.1393,
        "captured_at": current,
    }

    should_skip = user_locations._should_skip_location_update(
        existing=existing,
        lat=38.7222,
        lon=-9.1393,
        captured_at=current - timedelta(seconds=5),
    )

    assert should_skip is True


def test_should_skip_when_small_movement_within_throttle_window():
    current = datetime.now(timezone.utc)
    existing = {
        "lat": 38.7222,
        "lon": -9.1393,
        "captured_at": current,
    }

    should_skip = user_locations._should_skip_location_update(
        existing=existing,
        lat=38.72225,
        lon=-9.13935,
        captured_at=current + timedelta(seconds=60),
    )

    assert should_skip is True


def test_should_not_skip_when_significant_movement_detected():
    current = datetime.now(timezone.utc)
    existing = {
        "lat": 38.7222,
        "lon": -9.1393,
        "captured_at": current,
    }

    should_skip = user_locations._should_skip_location_update(
        existing=existing,
        lat=38.7240,
        lon=-9.1393,
        captured_at=current + timedelta(seconds=30),
    )

    assert should_skip is False


def test_should_skip_small_movement_even_after_long_delay():
    current = datetime.now(timezone.utc)
    existing = {
        "lat": 38.7222,
        "lon": -9.1393,
        "captured_at": current,
    }

    should_skip = user_locations._should_skip_location_update(
        existing=existing,
        lat=38.72225,
        lon=-9.13935,
        captured_at=current + timedelta(minutes=10),
    )

    assert should_skip is True
