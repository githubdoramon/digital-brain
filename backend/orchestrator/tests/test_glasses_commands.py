import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

import glasses_commands as commands
from auth import get_current_user
from routes.glasses_commands import create_glasses_commands_router


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSES_COMMAND_RECEIPT_DIR", str(tmp_path))
    monkeypatch.setenv("GLASSES_FRONT_GATE_SCRIPT", "script.example_front_gate")
    monkeypatch.setenv("GLASSES_CAR_GATE_SCRIPT", "script.example_vehicle_gate")
    monkeypatch.setenv("HA_URL", "https://ha.example.test")
    monkeypatch.setenv("HA_TOKEN", "fake-test-token")
    post = MagicMock(return_value=MagicMock(status_code=200))
    monkeypatch.setattr(commands.requests, "post", post)
    return post


def dispatch(command=commands.GlassesGateCommand.OPEN_FRONT_GATE, request_id=None, user="viewer@example.test", age=0):
    return commands.execute(user, command, request_id or uuid4(), int((time.time() - age) * 1000))


def test_fixed_script_selection_and_duplicate_request(configured):
    request_id = uuid4()
    assert dispatch(request_id=request_id)["status"] == "accepted"
    assert dispatch(request_id=request_id)["status"] == "accepted"
    configured.assert_called_once()
    call = configured.call_args
    assert call.args == ("https://ha.example.test/api/services/script/turn_on",)
    assert call.kwargs["json"] == {"entity_id": "script.example_front_gate"}
    assert call.kwargs["allow_redirects"] is False
    with pytest.raises(ValueError):
        dispatch(commands.GlassesGateCommand.TOGGLE_CAR_GATE, request_id)
    assert configured.call_count == 1


def test_car_gate_and_user_isolation(configured):
    request_id = uuid4()
    dispatch(commands.GlassesGateCommand.TOGGLE_CAR_GATE, request_id)
    assert configured.call_args.kwargs["json"] == {"entity_id": "script.example_vehicle_gate"}
    dispatch(commands.GlassesGateCommand.TOGGLE_CAR_GATE, request_id, "other@example.test")
    assert configured.call_count == 2


def test_simultaneous_duplicate_requests_dispatch_only_once(configured):
    request_id = uuid4()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: dispatch(request_id=request_id), range(4)))
    assert all(result["status"] == "accepted" for result in results)
    configured.assert_called_once()


@pytest.mark.parametrize("failure", [requests.Timeout(), requests.ConnectionError()])
def test_uncertain_network_outcome_never_repeats(configured, failure):
    configured.side_effect = failure
    request_id = uuid4()
    assert dispatch(request_id=request_id)["status"] == "unknown"
    assert dispatch(request_id=request_id)["status"] == "unknown"
    assert configured.call_count == 1


@pytest.mark.parametrize("code,expected", [(400, "failed"), (401, "failed"), (500, "unknown"), (302, "unknown")])
def test_http_outcomes_remain_safe_on_replay(configured, code, expected):
    configured.return_value.status_code = code
    request_id = uuid4()
    assert dispatch(request_id=request_id)["status"] == expected
    assert dispatch(request_id=request_id)["status"] == expected
    assert configured.call_count == 1


def test_expired_future_and_unconfigured_requests_never_operate_gate(configured, monkeypatch):
    assert dispatch(age=120)["status"] == "expired"
    assert dispatch(age=-30)["status"] == "expired"
    monkeypatch.setenv("GLASSES_FRONT_GATE_SCRIPT", "switch.example_relay")
    assert dispatch()["status"] == "not_configured"
    monkeypatch.setenv("GLASSES_FRONT_GATE_SCRIPT", "")
    assert dispatch()["status"] == "not_configured"
    configured.assert_not_called()


def test_reservation_survives_crash_after_dispatch(configured):
    configured.side_effect = KeyboardInterrupt()
    request_id = uuid4()
    with pytest.raises(KeyboardInterrupt):
        dispatch(request_id=request_id)
    configured.side_effect = None
    assert dispatch(request_id=request_id)["status"] == "unknown"
    assert configured.call_count == 1


def test_route_enforces_auth_and_fixed_payload(configured):
    app = FastAPI()
    app.include_router(create_glasses_commands_router())
    client = TestClient(app)
    body = {"command": "open_front_gate", "request_id": str(uuid4()), "captured_at_ms": int(time.time() * 1000)}
    assert client.post("/mobile/glasses/commands", json=body).status_code == 401
    configured.assert_not_called()
    app.dependency_overrides[get_current_user] = lambda: {"email": "viewer@example.test"}
    for invalid in [{**body, "command": "take_picture"}, {**body, "script": "script.example_other"}, {**body, "text": "private transcript"}]:
        assert client.post("/mobile/glasses/commands", json=invalid).status_code == 422
    configured.assert_not_called()
    assert client.post("/mobile/glasses/commands", json=body).json() == {"status": "accepted"}


def test_command_ids_are_not_home_assistant_script_entities(configured, monkeypatch):
    for command, key in commands.SCRIPT_ENV.items():
        monkeypatch.setenv(key, command.value)
        assert dispatch(command) == {
            "status": "not_configured",
            "configuration_issues": ["script_entity_invalid"],
        }
    configured.assert_not_called()


def test_configuration_diagnostics_distinguish_missing_credentials(configured, monkeypatch):
    monkeypatch.setenv("HA_URL", "")
    monkeypatch.setenv("HA_TOKEN", "")
    monkeypatch.setenv("GLASSES_FRONT_GATE_SCRIPT", "")
    assert dispatch()["configuration_issues"] == ["ha_url_missing", "ha_token_missing", "script_entity_missing"]
    configured.assert_not_called()
