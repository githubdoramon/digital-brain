from datetime import datetime, timezone

import event_media_suggestions


def test_suggestions_use_one_hour_default_and_retain_assets_without_gps(monkeypatch):
    captured = {}

    def search_assets_by_time(*, taken_after, taken_before):
        captured["after"] = taken_after
        captured["before"] = taken_before
        return [
            {
                "id": "asset:no-gps",
                "type": "IMAGE",
                "fileCreatedAt": "2026-08-30T10:30:00+00:00",
                "originalFileName": "memory.jpg",
            }
        ]

    monkeypatch.setattr(event_media_suggestions.immich_client, "search_assets_by_time", search_assets_by_time)

    result = event_media_suggestions.suggest_event_media(
        start_at=datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc),
        end_at=None,
        event_lat=38.7,
        event_lon=-9.1,
    )

    assert captured["after"].isoformat() == "2026-08-30T09:30:00+00:00"
    assert captured["before"].isoformat() == "2026-08-30T11:30:00+00:00"
    assert result[0]["asset_id"] == "asset:no-gps"
    assert result[0]["has_gps"] is False


def test_suggestions_filter_geo_tagged_assets_beyond_250m(monkeypatch):
    monkeypatch.setattr(
        event_media_suggestions.immich_client,
        "search_assets_by_time",
        lambda **kwargs: [
            {
                "id": "asset:near",
                "type": "VIDEO",
                "fileCreatedAt": "2026-08-30T10:15:00+00:00",
                "exifInfo": {"latitude": 38.7005, "longitude": -9.1005},
            },
            {
                "id": "asset:far",
                "type": "IMAGE",
                "fileCreatedAt": "2026-08-30T10:15:00+00:00",
                "exifInfo": {"latitude": 38.71, "longitude": -9.1},
            },
        ],
    )

    result = event_media_suggestions.suggest_event_media(
        start_at="2026-08-30T10:00:00+00:00",
        end_at="2026-08-30T11:00:00+00:00",
        event_lat=38.7,
        event_lon=-9.1,
    )

    assert [item["asset_id"] for item in result] == ["asset:near"]
    assert result[0]["media_type"] == "video"
