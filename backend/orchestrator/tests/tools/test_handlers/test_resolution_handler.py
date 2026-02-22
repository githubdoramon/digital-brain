from tools.handlers.resolution import (
    handle_lookup_contact,
    handle_lookup_contact_places,
    handle_lookup_place_contacts,
    handle_lookup_places,
    handle_select_contacts,
)


def test_select_contacts_group_selector(monkeypatch):
    monkeypatch.setattr(
        "contact_groups.resolve_group_members",
        lambda user_email, query, limit=120: {
            "found": True,
            "contacts": [
                {"contact_id": "contact-1", "display_name": "Ana"},
                {"contact_id": "contact-2", "display_name": "Bruno"},
            ],
        },
    )

    result = handle_select_contacts(
        {
            "action": "select",
            "selector_kind": "group",
            "value": "soccer team",
        },
        user_email="user@example.com",
    )

    assert result["action"] == "select"
    assert result["deterministic"] is True
    assert result["count"] == 2


def test_select_contacts_create_group(monkeypatch):
    monkeypatch.setattr(
        "contact_groups.create_contact_group",
        lambda **kwargs: {
            "group_id": "group:abc123",
            "owner_contact_id": "contact:user",
            "name": kwargs.get("name"),
            "description": kwargs.get("description"),
            "aliases": kwargs.get("aliases") or [],
            "member_count": len(kwargs.get("member_contact_ids") or []),
        },
    )

    result = handle_select_contacts(
        {
            "action": "create_group",
            "name": "soccer team",
            "member_contact_ids": ["contact-1", "contact-2"],
            "aliases": ["team"],
        },
        user_email="user@example.com",
    )

    assert result["action"] == "create_group"
    assert result["created"] is True
    assert result["group"]["name"] == "soccer team"


def test_lookup_contact_all_query_ignores_limit(monkeypatch):
    calls: dict[str, object] = {}

    def fake_search_contacts(query, search_by="any", fuzzy_threshold=75, limit=10):
        calls["query"] = query
        calls["limit"] = limit
        return [{"contact_id": "c1", "display_name": "Ana"}]

    monkeypatch.setattr("contacts.search_contacts", fake_search_contacts)

    result = handle_lookup_contact(
        {
            "action": "search",
            "query": "gmail.com",
            "limit": 100,
            "search_by": "email",
        },
        question="list all people with gmail emails",
    )

    assert result["found"] is True
    assert result["count"] == 1
    assert calls["query"] == "gmail.com"
    assert calls["limit"] is None


def test_select_contacts_all_query_uses_unbounded_limit(monkeypatch):
    calls: dict[str, object] = {}

    def fake_search_contacts_by_email_domain(value, limit=200):
        calls["value"] = value
        calls["limit"] = limit
        return [{"contact_id": "c1", "display_name": "Ana"}]

    monkeypatch.setattr(
        "contacts.search_contacts_by_email_domain", fake_search_contacts_by_email_domain
    )

    result = handle_select_contacts(
        {
            "action": "select",
            "selector_kind": "email_domain",
            "value": "gmail.com",
            "limit": 5,
            "auto_activate": False,
        },
        user_email="user@example.com",
        question="list all people with gmail emails",
    )

    assert result["count"] == 1
    assert calls["value"] == "gmail.com"
    assert calls["limit"] is None


def test_lookup_places_returns_ranked_matches(monkeypatch):
    monkeypatch.setattr(
        "places.search_places",
        lambda query, **_kwargs: [
            {
                "place_id": "plc_home",
                "name": "Home",
                "match_score": 98.0,
                "match_confidence": "high",
                "matched_via": "alias_exact",
            }
        ],
    )

    result = handle_lookup_places(
        {
            "query": "my house",
            "near_lat": 38.72,
            "near_lon": -9.13,
            "limit": 5,
        }
    )

    assert result["found"] is True
    assert result["count"] == 1
    assert result["places"][0]["place_id"] == "plc_home"


def test_lookup_contact_places_resolves_from_query(monkeypatch):
    monkeypatch.setattr(
        "contacts.search_contacts",
        lambda query, limit=3: [
            {
                "contact_id": "contact:jose",
                "display_name": "Jordan",
            }
        ],
    )
    monkeypatch.setattr(
        "places.list_contact_places",
        lambda contact_id, role_hint=None: [
            {
                "contact_id": contact_id,
                "place_id": "plc_home",
                "role": role_hint or "home",
                "name": "Home",
            }
        ],
    )
    monkeypatch.setattr(
        "places.resolve_contact_place",
        lambda **_kwargs: {
            "place_id": "plc_home",
            "name": "Home",
            "matched_via": "contact_place_relation",
            "confidence": "high",
        },
    )

    result = handle_lookup_contact_places(
        {
            "contact_query": "Jordan",
            "role_hint": "house",
            "where_text": "Jordan's house",
        }
    )

    assert result["found"] is True
    assert result["contact_id"] == "contact:jose"
    assert result["count"] == 1
    assert result["suggested_place"]["place_id"] == "plc_home"


def test_lookup_contact_places_supports_group_query(monkeypatch):
    monkeypatch.setattr(
        "contact_groups.resolve_group_members",
        lambda *_a, **_k: {
            "found": True,
            "group": {"group_id": "group:lewis", "name": "Lewis family"},
            "contacts": [
                {"contact_id": "contact:dana", "display_name": "Dana"},
                {"contact_id": "contact:ana", "display_name": "Ana"},
            ],
        },
    )

    def fake_list_contact_places(contact_id, role_hint=None):
        if contact_id == "contact:dana":
            return [{"place_id": "plc_home", "name": "Lewis Home", "role": "home"}]
        if contact_id == "contact:ana":
            return [{"place_id": "plc_home", "name": "Lewis Home", "role": "home"}]
        return []

    monkeypatch.setattr("places.list_contact_places", fake_list_contact_places)

    result = handle_lookup_contact_places(
        {
            "group_query": "Lewis family",
            "role_hint": "home",
        },
        user_email="user@example.com",
    )

    assert result["found"] is True
    assert result["group"]["group_id"] == "group:lewis"
    assert result["count"] == 1
    assert result["suggested_place"]["place_id"] == "plc_home"


def test_lookup_contact_places_falls_back_to_group_when_contact_missing(monkeypatch):
    monkeypatch.setattr("contacts.search_contacts", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "contact_groups.resolve_group_members",
        lambda *_a, **_k: {
            "found": True,
            "group": {"group_id": "group:lewis", "name": "Lewis family"},
            "contacts": [{"contact_id": "contact:dana", "display_name": "Dana"}],
        },
    )
    monkeypatch.setattr(
        "places.list_contact_places",
        lambda *_a, **_k: [{"place_id": "plc_home", "name": "Lewis Home", "role": "home"}],
    )

    result = handle_lookup_contact_places(
        {
            "contact_query": "Lewis family",
            "where_text": "where do they live",
        },
        user_email="user@example.com",
        question="Where do the Lewis family lives?",
    )

    assert result["found"] is True
    assert result["group"]["group_id"] == "group:lewis"
    assert result["role_hint"] == "home"


def test_lookup_place_contacts_resolves_place_query(monkeypatch):
    monkeypatch.setattr(
        "places.search_places",
        lambda *_a, **_k: [
            {
                "place_id": "plc_home",
                "name": "Lewis Home",
            }
        ],
    )
    monkeypatch.setattr(
        "places.list_place_contacts",
        lambda place_id, **_kwargs: [
            {
                "contact_id": "contact:dana",
                "display_name": "Dana",
                "role": "home",
                "place_id": place_id,
            }
        ],
    )

    result = handle_lookup_place_contacts(
        {
            "place_query": "Lewis home",
            "role_hint": "home",
            "limit": 10,
        }
    )

    assert result["found"] is True
    assert result["place_id"] == "plc_home"
    assert result["count"] == 1
    assert result["contacts"][0]["contact_id"] == "contact:dana"
