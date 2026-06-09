from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import get_current_user
from routes.contacts import create_contacts_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_contacts_router())
    app.dependency_overrides[get_current_user] = lambda: {"email": "user@example.test"}
    return TestClient(app)


def test_ingest_contact_route_returns_existing_contact_id(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "routes.contacts.contacts_service.ingest_contact",
        lambda contact: captured.append(contact),
    )

    response = _client().post(
        "/ingest/contact",
        json={
            "contact_id": "contact:alex-example",
            "display_name": "Alex Example",
            "emails": ["alex@example.test"],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "contact_id": "contact:alex-example",
        "created": False,
    }
    assert captured[0].contact_id == "contact:alex-example"


def test_ingest_contact_route_can_ensure_contact_id_from_email(monkeypatch):
    calls = []

    def fake_ensure_contact_for_email(email, *, display_name=None):
        calls.append((email, display_name))
        return "contact:alex-example", True

    monkeypatch.setattr(
        "routes.contacts.contacts_service.ensure_contact_for_email",
        fake_ensure_contact_for_email,
    )

    response = _client().post(
        "/ingest/contact",
        json={"email": "alex@example.test", "display_name": "Alex Example"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "contact_id": "contact:alex-example",
        "created": True,
    }
    assert calls == [("alex@example.test", "Alex Example")]
