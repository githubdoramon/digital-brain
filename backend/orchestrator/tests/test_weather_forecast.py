from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from weather_forecast import build_daily_weather_summary


def test_build_daily_weather_summary_formats_forecast():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "daily": {
            "weather_code": [2],
            "temperature_2m_min": [14.2],
            "temperature_2m_max": [21.1],
            "precipitation_probability_max": [20],
        }
    }
    mock_response.raise_for_status.return_value = None

    with patch("weather_forecast.requests.get", return_value=mock_response) as mock_get:
        summary = build_daily_weather_summary(
            location={"lat": 38.72, "lon": -9.14, "city": "Aurora"},
            target_date=date(2026, 3, 26),
            timezone_name="UTC",
        )

    assert "Weather in Aurora: partly cloudy" in summary
    assert "14C to 21C" in summary
    assert "rain chance up to 20%" in summary
    mock_get.assert_called_once()


def test_build_daily_weather_summary_returns_empty_on_missing_coords():
    summary = build_daily_weather_summary(
        location={"city": "Aurora"},
        target_date=date(2026, 3, 26),
        timezone_name="UTC",
    )

    assert summary == ""
