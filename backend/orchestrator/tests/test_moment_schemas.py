from __future__ import annotations

from pydantic import ValidationError

from schemas import MomentIn


def _payload() -> dict:
    return {
        "id": "7fe054c1-8f0d-4d63-b66d-bdd613a5c730",
        "source_type": "smart_glasses_image",
        "observed_at": "2026-08-31T12:10:09Z",
        "observed_timezone": "Europe/Lisbon",
        "observed_utc_offset_minutes": 60,
        "observation": {
            "schema_version": "moment_observation.v1",
            "summary": "A person works at a desk.",
            "objects": [{"label": "laptop", "count_min": 1, "count_max": 1, "details": []}],
            "visible_text": ["Draft"],
            "people_presence": "present",
            "people_count_min": 1,
            "people_count_max": 1,
            "people_details": [],
            "setting": "office",
            "interpretations": [],
            "uncertainties": [],
            "person_identification_attempted": False,
        },
        "location": {"lat": 38.7, "lon": -9.1, "provenance": "phone_location_history"},
    }


def test_moment_schema_accepts_canonical_observation() -> None:
    moment = MomentIn.model_validate(_payload())

    assert moment.id == "7fe054c1-8f0d-4d63-b66d-bdd613a5c730"
    assert moment.observation.schema_version == "moment_observation.v1"
    assert moment.location and moment.location.provenance == "phone_location_history"


def test_moment_schema_rejects_legacy_observation_version() -> None:
    payload = _payload()
    payload["observation"]["schema_version"] = "visual_observation.v2"

    try:
        MomentIn.model_validate(payload)
    except ValidationError as exc:
        assert "moment_observation.v1" in str(exc)
    else:
        raise AssertionError("Expected schema validation failure")
