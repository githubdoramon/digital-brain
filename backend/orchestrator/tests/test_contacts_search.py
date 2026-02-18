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
        return {"contact-2": 0.2}

    def fake_load(contact_ids=None):
        calls["loaded_ids"] = contact_ids
        return [
            _contact("contact-1", "John Smith"),
            _contact("contact-2", "Alice Doe"),
        ]

    def fail_if_full_scan():
        raise AssertionError("full scan fallback should not run when prefiltered candidates match")

    monkeypatch.setattr(contacts, "_lexical_candidate_contact_ids", fake_lexical)
    monkeypatch.setattr(contacts, "_vector_candidate_contact_scores", fake_vector)
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
    monkeypatch.setattr(contacts, "_vector_candidate_contact_scores", lambda *_a, **_k: {})
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
    monkeypatch.setattr(
        contacts, "_load_contacts", lambda *_a, **_k: [_contact("contact-1", "Jane")]
    )
    monkeypatch.setattr(contacts, "list_contacts", lambda: [])

    def fake_vector(*_args, **_kwargs):
        calls["vector_called"] += 1
        return {"contact-1": 0.9}

    monkeypatch.setattr(contacts, "_vector_candidate_contact_scores", fake_vector)

    contacts.search_contacts("jane@example.com", search_by="email", limit=5)

    assert calls["vector_called"] == 0


def test_search_contacts_any_non_email_query_ignores_email_domain_noise(monkeypatch):
    def fake_lexical(*_args, **_kwargs):
        return ["contact-email"]

    def fake_vector(*_args, **_kwargs):
        # Vector picks the role-based contact as semantically relevant.
        return {"contact-role": 0.9}

    def fake_load(contact_ids=None):
        assert contact_ids == ["contact-email", "contact-role"]
        return [
            _contact("contact-email", "Alice Example", emails=["alice@acme.example"]),
            _contact(
                "contact-role",
                "Dana Executive",
                comments="Chief Executive Officer at Acme",
            ),
        ]

    monkeypatch.setattr(contacts, "_lexical_candidate_contact_ids", fake_lexical)
    monkeypatch.setattr(contacts, "_vector_candidate_contact_scores", fake_vector)
    monkeypatch.setattr(contacts, "_load_contacts", fake_load)
    monkeypatch.setattr(contacts, "list_contacts", lambda: [])

    results = contacts.search_contacts("acme's ceo", search_by="any", limit=5)

    assert len(results) == 1
    assert results[0]["contact_id"] == "contact-role"
    assert results[0]["match_reason"].startswith("vector match")


def test_is_email_intent_query_detects_at_or_email_word():
    assert contacts._is_email_intent_query("john@example.com") is True
    assert contacts._is_email_intent_query("john email") is True
    assert contacts._is_email_intent_query("acme's ceo") is False


def test_search_contacts_enforces_minimum_confidence_floor(monkeypatch):
    monkeypatch.setattr(contacts, "_lexical_candidate_contact_ids", lambda *_a, **_k: ["contact-1"])
    monkeypatch.setattr(contacts, "_vector_candidate_contact_scores", lambda *_a, **_k: {})
    monkeypatch.setattr(
        contacts,
        "_load_contacts",
        lambda *_a, **_k: [_contact("contact-1", "John Smith")],
    )
    monkeypatch.setattr(contacts, "list_contacts", lambda: [])

    # Force fuzzy score below 0.6 confidence (score < 60).
    from rapidfuzz import fuzz

    monkeypatch.setattr(fuzz, "token_sort_ratio", lambda *_a, **_k: 55)

    results = contacts.search_contacts("jhn smt", search_by="name", fuzzy_threshold=40, limit=5)
    assert results == []


def test_search_contacts_rejects_low_vector_confidence(monkeypatch):
    monkeypatch.setattr(contacts, "_lexical_candidate_contact_ids", lambda *_a, **_k: [])
    monkeypatch.setattr(contacts, "_vector_candidate_contact_scores", lambda *_a, **_k: {"c1": 0.4})
    monkeypatch.setattr(
        contacts,
        "_load_contacts",
        lambda *_a, **_k: [
            _contact("c1", "Low Vector", comments="Chief Executive Officer at Acme")
        ],
    )
    monkeypatch.setattr(contacts, "list_contacts", lambda: [])

    results = contacts.search_contacts("CEO at Acme", search_by="name", limit=5)
    assert results == []


def test_search_contacts_comment_weight_can_beat_email_match(monkeypatch):
    monkeypatch.setattr(
        contacts,
        "_lexical_candidate_contact_ids",
        lambda *_a, **_k: ["contact-email", "contact-comment"],
    )
    monkeypatch.setattr(contacts, "_vector_candidate_contact_scores", lambda *_a, **_k: {})
    monkeypatch.setattr(
        contacts,
        "_load_contacts",
        lambda *_a, **_k: [
            _contact(
                "contact-email",
                "Email Person",
                emails=["exec@acme.example"],
                comments="",
            ),
            _contact(
                "contact-comment",
                "Comment Person",
                emails=[],
                comments="@acme.example",
            ),
        ],
    )
    monkeypatch.setattr(contacts, "list_contacts", lambda: [])

    # Includes "@" to activate email-intent scoring in "any" mode.
    results = contacts.search_contacts("@acme.example", search_by="any", limit=5)
    assert len(results) >= 1
    assert results[0]["contact_id"] == "contact-comment"
    assert results[0]["match_reason"] == "comment match"


def test_search_contacts_with_none_limit_returns_all_matches(monkeypatch):
    monkeypatch.setattr(contacts, "_lexical_candidate_contact_ids", lambda *_a, **_k: [])
    monkeypatch.setattr(contacts, "_vector_candidate_contact_scores", lambda *_a, **_k: {})
    monkeypatch.setattr(
        contacts,
        "list_contacts",
        lambda: [
            _contact("c1", "Ana", emails=["ana@gmail.com"]),
            _contact("c2", "Bruno", emails=["bruno@gmail.com"]),
            _contact("c3", "Carla", emails=["carla@gmail.com"]),
        ],
    )

    results = contacts.search_contacts("gmail.com", search_by="email", limit=None)

    assert len(results) == 3
