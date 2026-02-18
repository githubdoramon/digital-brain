from tools.handlers.resolution import handle_lookup_contact, handle_select_contacts


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
