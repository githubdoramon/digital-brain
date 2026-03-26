from __future__ import annotations

from unittest.mock import patch


class TestNotificationPreferences:
    @patch("notifications.preferences.upsert_subscription")
    @patch("notifications.preferences.list_notification_types")
    @patch("notifications.preferences.get_notification_type_title")
    def test_update_notification_channels_per_type(
        self,
        mock_get_title,
        mock_list_types,
        mock_upsert,
    ):
        import notifications.preferences as preferences

        mock_list_types.return_value = ["daily-briefing", "emergency-stock"]
        mock_get_title.return_value = "Daily briefing ready"
        mock_upsert.return_value = {
            "notification_channels": ["email", "push"],
            "created_at": None,
            "updated_at": None,
        }

        result = preferences.update_notification_channels(
            "user@example.com",
            "daily-briefing",
            [" PUSH ", "email", "push"],
        )

        mock_upsert.assert_called_once_with(
            "user@example.com",
            "daily-briefing",
            ["email", "push"],
        )
        assert result["notification_type"] == "daily-briefing"
        assert result["enabled"] is True
        assert result["channels"] == ["email", "push"]

    @patch("notifications.preferences.list_notification_types")
    def test_update_notification_channels_rejects_unknown_type(self, mock_list_types):
        import notifications.preferences as preferences

        mock_list_types.return_value = ["daily-briefing"]

        try:
            preferences.update_notification_channels(
                "user@example.com",
                "unknown-type",
                ["push"],
            )
            raise AssertionError("Expected ValueError")
        except ValueError as exc:
            assert "Unknown notification type" in str(exc)
