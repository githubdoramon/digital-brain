from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import get_current_user
from routes.system import create_system_router


def create_client() -> TestClient:
    app = FastAPI()
    app.include_router(create_system_router())
    app.dependency_overrides[get_current_user] = lambda: {"email": "user@example.com"}
    return TestClient(app)


def test_list_notification_devices(monkeypatch):
    client = create_client()

    monkeypatch.setattr(
        "routes.system.devices.list_user_devices",
        lambda email: [
            {
                "device_id": "device:test-1",
                "user_email": email,
                "expo_push_token": "ExponentPushToken[test-token-1]",
                "platform": "android",
                "device_name": "Pixel Test",
                "app_version": "1.2.3",
                "os_version": "14",
                "created_at": None,
                "updated_at": None,
                "last_seen_at": None,
            }
        ],
    )

    response = client.get("/system/notifications/devices")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["devices"]) == 1
    assert payload["devices"][0]["deviceId"] == "device:test-1"
    assert payload["devices"][0]["expoPushToken"] == "ExponentPushToken[test-token-1]"


def test_send_test_notification(monkeypatch):
    client = create_client()

    monkeypatch.setattr(
        "routes.system.devices.get_user_device",
        lambda email, device_id: {
            "device_id": device_id,
            "user_email": email,
            "expo_push_token": "ExponentPushToken[test-token-2]",
            "platform": "ios",
            "device_name": "iPhone Test",
            "app_version": "1.2.3",
            "os_version": "18.0",
            "created_at": None,
            "updated_at": None,
            "last_seen_at": None,
        },
    )
    monkeypatch.setattr(
        "routes.system.send_push_notification",
        lambda title, message, tokens: {
            "sent": 1,
            "success": 1,
            "errors": [],
            "tickets": [
                {
                    "token": tokens[0],
                    "status": "ok",
                    "id": "ticket-123",
                }
            ],
        },
    )

    response = client.post(
        "/system/notifications/test",
        json={
            "deviceId": "device:test-2",
            "title": "Test title",
            "message": "Test message",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["device"]["deviceId"] == "device:test-2"
    assert payload["sent"] == 1
    assert payload["success"] == 1
    assert payload["tickets"][0]["id"] == "ticket-123"
