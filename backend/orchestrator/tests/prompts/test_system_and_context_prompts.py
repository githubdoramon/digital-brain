import sys

from prompts.context import get_self_context
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
                "emails": ["user@example.com", "redacted@example.invalid"],
                "aliases": ["Ramon", "R. Canales"],
            }

    monkeypatch.setitem(sys.modules, "contacts", _ContactsModule())
    context = get_self_context("user@example.com")
    assert context is not None
    assert "Known user emails" in context
    assert "user@example.com" in context
    assert "redacted@example.invalid" in context
    assert "Known user aliases" in context
    assert "R. Canales" in context
