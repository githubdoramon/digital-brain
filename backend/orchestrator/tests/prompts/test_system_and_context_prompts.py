import sys

from prompts.context import get_location_context, get_self_context
from prompts.system import get_bounded_agent_protocol


def test_self_context_includes_counterpart_guardrail():
    context = get_self_context("user@example.com")
    assert context is not None
    assert "owner/narrator of this memory graph" in context
    assert "return counterpart contacts, not the user themselves" in context


def test_bounded_protocol_includes_self_identity_guardrail():
    protocol = get_bounded_agent_protocol()
    assert "Self-identity guardrail" in protocol
    assert "do NOT return the user as the counterpart" in protocol


def test_self_context_includes_known_aliases_and_emails(monkeypatch):
    class _ContactsModule:
        @staticmethod
        def find_self_contact(_email):
            return {
                "display_name": "Alex Carter",
                "emails": ["alex@example.com", "acarter@example.com"],
                "aliases": ["Alex", "A. Carter"],
            }

    monkeypatch.setitem(sys.modules, "contacts", _ContactsModule())
    context = get_self_context("alex@example.com")
    assert context is not None
    assert "Known user emails" in context
    assert "alex@example.com" in context
    assert "acarter@example.com" in context
    assert "Known user aliases" in context
    assert "A. Carter" in context


def test_location_context_includes_inferred_place_details():
    context = get_location_context(
        {
            "timezone": "UTC",
            "location": {
                "lat": 38.722,
                "lon": -9.139,
                "accuracy_m": 22.4,
            },
            "inferred_location": {
                "place_name": "Home",
                "city": "Aurora",
                "country": "Westoria",
                "source": "known_place_proximity",
                "confidence": "high",
                "distance_m": 15.2,
            },
        }
    )
    assert context is not None
    assert "Likely current place: Home" in context
    assert "Place inference source: known_place_proximity" in context
    assert "Distance to inferred place: 15.2 meters" in context


def test_location_context_includes_recent_resolved_place_guidance():
    context = get_location_context(
        {
            "recent_resolved_place": {
                "place_id": "plc_home_123",
                "place_name": "Morgan's apt",
                "address": "Maple Street, 23, Springfield",
                "role_hint": "home",
            }
        }
    )
    assert context is not None
    assert "place_id: plc_home_123" in context
    assert "recent resolved place is available" in context
