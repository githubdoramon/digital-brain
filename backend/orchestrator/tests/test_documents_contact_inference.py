from types import SimpleNamespace

import documents


def test_infer_document_contact_ids_uses_contact_resolution(monkeypatch):
    captured = {}

    def fake_resolve_contacts_request(payload):
        captured.update(payload)
        return {
            "resolved_contacts": [
                {"contact_id": "contact:daughter", "display_name": "Daughter"},
                {"contact_id": "contact:daughter", "display_name": "Daughter"},
            ]
        }

    monkeypatch.setitem(
        __import__("sys").modules,
        "contact_resolution_service",
        SimpleNamespace(resolve_contacts_request=fake_resolve_contacts_request),
    )

    result = documents._infer_document_contact_ids(
        user_email="user@example.com",
        title="Glasses prescription",
        description="Prescription for my daughter",
        file_name="glasses.pdf",
        content="PD 54 frame width 115",
    )

    assert result == ["contact:daughter"]
    assert captured["user_email"] == "user@example.com"
    assert captured["mode"] == "minimal"
    assert "my daughter" in captured["text"].lower()


def test_merge_document_contact_ids_preserves_order_and_dedupes():
    result = documents._merge_document_contact_ids(
        ["contact:self", "contact:daughter"],
        ["contact:daughter", "contact:doctor"],
    )

    assert result == ["contact:self", "contact:daughter", "contact:doctor"]
