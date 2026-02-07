from __future__ import annotations

from typing import Any

import contacts


def _contact(contact_id: str, display_name: str, **overrides: Any) -> dict[str, Any]:
    base = {
        "contact_id": contact_id,
        "display_name": display_name,
        "aliases": [],
        "birthday": None,
        "emails": [],
        "phones": [],
        "links": [],
        "tags": [],
        "comments": "",
        "external_id": None,
        "avatar_url": None,
        "relationships": [],
    }
    base.update(overrides)
    return base


def test_search_contacts_prefiltered_candidates_without_full_fallback(monkeypatch):
    calls: dict[str, Any] = {}

    def fake_lexical(*_args, **_kwargs):
        return ["contact-1"]

    def fake_vector(*_args, **_kwargs):
        calls["vector_called"] = True
        return ["contact-2"]

    def fake_load(contact_ids=None):
        calls["loaded_ids"] = contact_ids
        return [
            _contact("contact-1", "John Smith"),
            _contact("contact-2", "Alice Doe"),
        ]

    def fail_if_full_scan():
        raise AssertionError("full scan fallback should not run when prefiltered candidates match")

    monkeypatch.setattr(contacts, "_lexical_candidate_contact_ids", fake_lexical)
    monkeypatch.setattr(contacts, "_vector_candidate_contact_ids", fake_vector)
    monkeypatch.setattr(contacts, "_load_contacts", fake_load)
    monkeypatch.setattr(contacts, "list_contacts", fail_if_full_scan)

    results = contacts.search_contacts("john", search_by="name", limit=5)

    assert calls.get("vector_called") is True
    assert calls.get("loaded_ids") == ["contact-1", "contact-2"]
    assert len(results) == 1
    assert results[0]["contact_id"] == "contact-1"


def test_search_contacts_falls_back_to_full_scan_when_prefilter_misses(monkeypatch):
    calls = {"full_scan_count": 0}

    monkeypatch.setattr(contacts, "_lexical_candidate_contact_ids", lambda *_a, **_k: ["contact-1"])
    monkeypatch.setattr(contacts, "_vector_candidate_contact_ids", lambda *_a, **_k: [])
    monkeypatch.setattr(
        contacts,
        "_load_contacts",
        lambda *_a, **_k: [_contact("contact-1", "Alice Doe")],
    )

    def fake_full_scan():
        calls["full_scan_count"] += 1
        return [_contact("contact-9", "John Smith")]

    monkeypatch.setattr(contacts, "list_contacts", fake_full_scan)

    results = contacts.search_contacts("john", search_by="name", limit=5)

    assert calls["full_scan_count"] == 1
    assert len(results) == 1
    assert results[0]["contact_id"] == "contact-9"


def test_search_contacts_email_mode_skips_vector_candidates(monkeypatch):
    calls = {"vector_called": 0}

    monkeypatch.setattr(contacts, "_lexical_candidate_contact_ids", lambda *_a, **_k: ["contact-1"])
    monkeypatch.setattr(contacts, "_load_contacts", lambda *_a, **_k: [_contact("contact-1", "Jane")])
    monkeypatch.setattr(contacts, "list_contacts", lambda: [])

    def fake_vector(*_args, **_kwargs):
        calls["vector_called"] += 1
        return ["contact-1"]

    monkeypatch.setattr(contacts, "_vector_candidate_contact_ids", fake_vector)

    contacts.search_contacts("jane@example.com", search_by="email", limit=5)

    assert calls["vector_called"] == 0
