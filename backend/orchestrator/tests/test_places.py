import places


def test_find_best_place_match_uses_alias_and_synonym(monkeypatch):
    monkeypatch.setattr(
        "places._list_places",
        lambda: [
            {
                "place_id": "plc_home",
                "name": "Home",
                "aliases": ["my home"],
                "city": "Aurora",
                "country": "Westoria",
                "lat": 38.722,
                "lon": -9.139,
            }
        ],
    )

    match = places.find_best_place_match("my house")

    assert match is not None
    assert match.get("place_id") == "plc_home"
    assert match.get("match_confidence") == "high"


def test_find_best_place_match_returns_none_below_threshold(monkeypatch):
    monkeypatch.setattr(
        "places._list_places",
        lambda: [
            {
                "place_id": "plc_office",
                "name": "Acme HQ",
                "aliases": [],
                "city": None,
                "country": None,
                "lat": None,
                "lon": None,
            }
        ],
    )

    match = places.find_best_place_match("random mountain", fuzzy_threshold=90)
    assert match is None


def test_resolve_contact_place_prefers_matching_role(monkeypatch):
    monkeypatch.setattr(
        "places.list_contact_places",
        lambda *_a, **_k: [
            {
                "contact_id": "contact:jose",
                "place_id": "plc_work",
                "role": "work",
                "confidence": "high",
                "name": "Acme Office",
                "aliases": ["office"],
                "city": "Aurora",
                "country": "Westoria",
                "lat": None,
                "lon": None,
            },
            {
                "contact_id": "contact:jose",
                "place_id": "plc_home",
                "role": "home",
                "confidence": "high",
                "name": "Home",
                "aliases": ["my house"],
                "city": "Aurora",
                "country": "Westoria",
                "lat": None,
                "lon": None,
            },
        ],
    )

    match = places.resolve_contact_place(
        contact_id="contact:jose",
        role_hint="house",
        where_text="Jordan's house",
    )

    assert match is not None
    assert match.get("place_id") == "plc_home"
    assert match.get("matched_via") == "contact_place_relation"
