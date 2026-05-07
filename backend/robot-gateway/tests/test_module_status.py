"""Tests for derived module presence/status."""

from datetime import UTC, datetime, timedelta

from module_status import derive_module_status


def test_recent_module_without_explicit_status_is_online():
    now = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    assert (
        derive_module_status(
            last_seen_at=now - timedelta(seconds=5),
            reported_status="offline",
            status_updated_at=None,
            now=now,
        )
        == "online"
    )


def test_stale_module_is_offline_after_30_seconds():
    now = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    assert (
        derive_module_status(
            last_seen_at=now - timedelta(seconds=31),
            reported_status="online",
            status_updated_at=now - timedelta(seconds=31),
            now=now,
        )
        == "offline"
    )


def test_recent_explicit_offline_beats_fresh_last_seen():
    now = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    last_seen = now - timedelta(seconds=2)
    assert (
        derive_module_status(
            last_seen_at=last_seen,
            reported_status="offline",
            status_updated_at=last_seen,
            now=now,
        )
        == "offline"
    )


def test_recent_error_is_preserved_while_module_is_fresh():
    now = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    assert (
        derive_module_status(
            last_seen_at=now - timedelta(seconds=3),
            reported_status="error",
            status_updated_at=now - timedelta(seconds=3),
            now=now,
        )
        == "error"
    )
