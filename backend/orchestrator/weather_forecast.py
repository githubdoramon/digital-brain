from __future__ import annotations

from datetime import date
from typing import Any

import requests

from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

_WEATHER_CODE_LABELS: dict[int, str] = {
    0: "clear skies",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "dense fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "heavy freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "heavy freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "rain showers",
    81: "heavy rain showers",
    82: "violent rain showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorms",
    96: "thunderstorms with hail",
    99: "severe thunderstorms with hail",
}


def build_daily_weather_summary(
    *,
    location: dict[str, Any],
    target_date: date,
    timezone_name: str,
) -> str:
    lat = _to_float(location.get("lat"))
    lon = _to_float(location.get("lon"))
    if lat is None or lon is None:
        return ""

    try:
        response = requests.get(
            _OPEN_METEO_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "weather_code,temperature_2m_min,temperature_2m_max,precipitation_probability_max",
                "timezone": timezone_name,
                "start_date": target_date.isoformat(),
                "end_date": target_date.isoformat(),
            },
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        logger.warning("[weather_forecast] Failed to fetch forecast", exc_info=True)
        return ""

    daily = payload.get("daily")
    if not isinstance(daily, dict):
        return ""

    weather_code = _first_value(daily.get("weather_code"))
    min_temp = _to_float(_first_value(daily.get("temperature_2m_min")))
    max_temp = _to_float(_first_value(daily.get("temperature_2m_max")))
    precip_max = _to_float(_first_value(daily.get("precipitation_probability_max")))

    label = _WEATHER_CODE_LABELS.get(int(weather_code), "mixed conditions") if weather_code is not None else "mixed conditions"
    temp_text = _format_temp_range(min_temp, max_temp)
    rain_text = ""
    if precip_max is not None:
        rain_text = f", rain chance up to {round(precip_max)}%"

    location_label = _format_location_label(location)
    return f"Weather in {location_label}: {label}, {temp_text}{rain_text}."


def _first_value(value: Any) -> Any:
    if isinstance(value, list) and value:
        return value[0]
    return value


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_temp_range(min_temp: float | None, max_temp: float | None) -> str:
    if min_temp is not None and max_temp is not None:
        return f"{round(min_temp)}C to {round(max_temp)}C"
    if max_temp is not None:
        return f"high near {round(max_temp)}C"
    if min_temp is not None:
        return f"low near {round(min_temp)}C"
    return "temperature unavailable"


def _format_location_label(location: dict[str, Any]) -> str:
    for key in ("place_name", "city"):
        value = str(location.get(key) or "").strip()
        if value:
            return value
    country = str(location.get("country") or "").strip()
    if country:
        return country
    return "your area"
