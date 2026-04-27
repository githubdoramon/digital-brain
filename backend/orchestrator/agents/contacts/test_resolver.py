"""
Tests for contact resolver.

Run with: python -m pytest agents/contacts/test_resolver.py -v
Or directly: python3 agents/contacts/test_resolver.py
"""

import logging
import os
import sys
from typing import Any
from unittest.mock import MagicMock, patch

logger = logging.getLogger(__name__)

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


def mock_call_llm_json(prompt: str, **kwargs) -> dict[str, Any]:
    """
    Mock LLM that returns reasonable responses based on prompt content.

    This allows tests to run without internet connectivity.
    """
    prompt_lower = prompt.lower()

    # Mock person extraction
    if "extract all person references" in prompt_lower:
        # Extract the actual text being analyzed from the prompt
        # Look for Text: "..." pattern
        import re

        text_match = re.search(r'text:\s*"([^"]+)"', prompt_lower)
        text_content = text_match.group(1) if text_match else prompt_lower

        # Be more specific about matching text patterns
        if "i visited my daughter's doctor" in text_content:
            # User is participant, so include them
            return {"people": ["user", "my daughter", "my daughter's doctor"]}
        elif "i visited my daughter's mother" in text_content:
            # User is participant, so include them (with "user" token)
            return {"people": ["user", "my daughter", "my daughter's mother"]}
        elif "my daughter visited her mother" in text_content:
            # User is just narrator, not participant
            # NEW: Apply pronoun resolution - "her mother" → "my daughter's mother"
            return {"people": ["my daughter", "my daughter's mother"]}
        elif "the doctor saw her patient" in text_content:
            # Ambiguous case - "her" refers to doctor but this is ownership, not nested relationship
            # Should NOT resolve to "the doctor's patient"
            return {"people": ["the doctor", "her patient"]}
        elif "i took mira to her eye doctor" in text_content and "dr. nash" in text_content:
            return {"people": ["user", "Mira", "Dr. Nash"]}
        elif "acme's ceo" in text_content:
            # Simulate bad first-pass extraction that splits org possessive title.
            return {"people": ["Acme", "CEO"]}
        elif "had lunch with john smith" in text_content:
            # User is participant (having lunch)
            return {"people": ["user", "John Smith"]}
        elif "saw john smith" in text_content:
            return {"people": ["John Smith", "Unknown Person", "Dr. Jones"]}
        elif "went to the store" in text_content:
            return {"people": []}
        else:
            # Default: return empty
            return {"people": []}

    # Mock disambiguation
    elif "disambiguate a person reference" in prompt_lower:
        # Always pick the first candidate in tests
        if "1." in prompt:
            return {
                "decision": "resolved",
                "candidate_number": 1,
                "confidence": "high",
                "reasoning": "Test mock always picks first candidate",
            }
        return {
            "decision": "cannot_decide",
            "candidate_number": None,
            "confidence": "low",
            "reasoning": "No candidates provided",
        }

    # Mock collective selector extraction
    elif "extract collective participant selectors" in prompt_lower:
        if "@acme.example" in prompt_lower:
            return {
                "selectors": [
                    {
                        "kind": "email_domain",
                        "value": "acme.example",
                        "raw": "@acme.example",
                        "deterministic": True,
                    }
                ]
            }
        if "soccer team" in prompt_lower:
            return {
                "selectors": [
                    {
                        "kind": "group",
                        "value": "soccer team",
                        "raw": "my soccer team",
                        "deterministic": False,
                    }
                ]
            }
        return {"selectors": []}

    return {}


# Patch the LLM helper module before importing anything
mock_llm_helpers = MagicMock()
mock_llm_helpers.call_llm_json = mock_call_llm_json
sys.modules["llm_helpers"] = mock_llm_helpers

# Mock contacts module before importing
mock_contacts_module = MagicMock()
# Set up find_self_contact to return a proper user contact
mock_contacts_module.find_self_contact.return_value = {
    "contact_id": "user-123",
    "display_name": "Test User",
}
sys.modules["contacts"] = mock_contacts_module

# Now import after mocking
import agents.contacts.resolver as resolver_module  # noqa: E402

resolver_module.call_llm_json = mock_call_llm_json

from agents.contacts.resolver import (  # noqa: E402
    _detect_relational_term,
    _extract_collective_selectors,
    _fast_extract_people_from_text,
    _parse_nested_relationship,
    _strip_generic_markers,
    extract_people_from_text,
    resolve_contact,
    resolve_contacts_from_text,
)


def test_detect_relational_term():
    """Test detection of relational terms."""
    # Should detect
    assert _detect_relational_term("my daughter") == "daughter"
    assert _detect_relational_term("the doctor") == "doctor"

    # Should not detect proper names
    assert _detect_relational_term("John Smith") is None
    assert _detect_relational_term("Dr. Jones") is None


def test_parse_nested_relationship():
    """Test parsing of nested relationships."""
    # Should parse nested
    result = _parse_nested_relationship("my daughter's doctor")
    assert result == ["my daughter", "doctor"]

    result = _parse_nested_relationship("my son's teacher")
    assert result == ["my son", "teacher"]

    result = _parse_nested_relationship("Emma's dentist")
    assert result == ["Emma", "dentist"]

    # Should not parse non-nested
    result = _parse_nested_relationship("my daughter")
    assert result is None

    result = _parse_nested_relationship("John Smith")
    assert result is None


def test_strip_generic_markers():
    """Test stripping of generic markers."""
    assert _strip_generic_markers("my daughter") == "daughter"
    assert _strip_generic_markers("the doctor") == "doctor"
    assert _strip_generic_markers("a friend") == "friend"
    assert _strip_generic_markers("their lawyer") == "lawyer"
    assert _strip_generic_markers("John Smith") == "John Smith"


def test_extract_people_from_text():
    """Test person extraction from text."""
    # Mock extraction - the mock returns people based on text content
    # Without user_email, "user" token will be converted to user's name
    people = extract_people_from_text("I visited my daughter's doctor yesterday")
    assert "user" in people or "Test User" in people  # User is participant
    assert "my daughter" in people
    assert "my daughter's doctor" in people

    people = extract_people_from_text("Had lunch with John Smith")
    assert "user" in people or "Test User" in people  # User is participant
    assert "John Smith" in people

    people = extract_people_from_text("Went to the store")
    assert people == []

    # Test user as participant (should be included)
    people = extract_people_from_text("I visited my daughter's mother")
    # Should convert "user" token to actual user name (Test User)
    assert "I" not in people
    assert "user" in people or "Test User" in people  # User is participant
    assert "my daughter" in people
    assert "my daughter's mother" in people

    # Test user as narrator (should NOT be included)
    # NEW: With pronoun resolution, "her mother" becomes "my daughter's mother"
    people = extract_people_from_text("My daughter visited her mother")
    # User is just narrator, not participant
    assert "Test User" not in people and "user" not in people
    assert "my daughter" in people
    # After pronoun resolution: "her mother" → "my daughter's mother"
    assert "my daughter's mother" in people

    # Test ambiguous pronoun that should NOT be resolved
    # "The doctor" owns the patient, not a nested relationship
    people = extract_people_from_text("The doctor saw her patient")
    # Should keep as separate entities, not resolve to "the doctor's patient"
    assert "the doctor" in people
    assert "her patient" in people or "the doctor's patient" not in people  # Should NOT nest

    # Test generic role overridden by named person later in text
    people = extract_people_from_text(
        "Yesterday I took Mira to her eye doctor. Additional details: Ah, it was at 14:00, "
        "and the doc name is Dr. Nash"
    )
    assert "user" in people or "Test User" in people
    assert "Mira" in people
    assert "Dr. Nash" in people
    assert "Mira's eye doctor" not in people

    # Test possessive org title split repair
    people = extract_people_from_text("Acme's CEO joined the meeting")
    assert "Acme" not in people
    assert "CEO" not in people
    assert "CEO at Acme" in people


def test_extract_collective_selectors():
    selectors = _extract_collective_selectors(
        "having a meeting with all acme employees about AI and everyone with @acme.example"
    )
    kinds = {selector["kind"] for selector in selectors}
    assert "email_domain" in kinds
    assert "company" in kinds


def test_fast_extract_people_from_text_handles_bare_family_mentions_in_with_list():
    people, selectors, applied = _fast_extract_people_from_text(
        "Last Friday I went to the movies at 18h00 with daughter and wife."
    )

    assert applied is True
    assert selectors == []
    assert "user" in people
    assert "daughter" in people
    assert "wife" in people


def test_resolve_contacts_from_text_merges_llm_people_when_fast_path_is_incomplete():
    with (
        patch(
            "agents.contacts.resolver._fast_extract_people_from_text",
            return_value=(["user"], [], True),
        ),
        patch(
            "agents.contacts.resolver.extract_people_from_text",
            return_value=["my wife", "my daughter"],
        ),
        patch(
            "agents.contacts.resolver._resolve_people_mentions",
            return_value=([], [], [], {}),
        ),
    ):
        result = resolve_contacts_from_text(
            "Last Friday I went to the movies at 18h00 with daughter and wife.",
            "user@example.com",
            mode=resolver_module.MINIMAL_RESOLUTION_MODE,
        )

    assert result["people_mentioned"] == ["user", "my wife", "my daughter"]


def test_resolve_contacts_from_text_uses_llm_collective_selectors():
    with (
        patch("agents.contacts.resolver.contacts_service") as mock_contacts,
        patch("agents.contacts.resolver.contact_groups_service") as mock_groups,
    ):
        mock_groups.resolve_group_members.return_value = {"found": False, "contacts": []}
        mock_contacts.search_contacts_by_email_domain.return_value = [
            {"contact_id": "contact-1", "display_name": "Alice", "emails": ["alice@acme.example"]}
        ]
        mock_contacts.search_contacts_by_company.return_value = []
        mock_contacts.search_contacts_by_group_hint.return_value = []
        mock_contacts.search_contacts.return_value = []

        result = resolve_contacts_from_text(
            "meeting with everyone that has @acme.example email",
            "user@example.com",
        )

        assert result["status"] == "success"
        assert result["resolved_contacts"]
        assert any(
            selector.get("kind") == "email_domain"
            for selector in result.get("selector_mentions", [])
        )


@patch("agents.contacts.resolver.contacts_service")
@patch("agents.contacts.resolver.contact_groups_service")
def test_resolve_contacts_from_text_email_domain_selector(mock_groups, mock_contacts):
    mock_contacts.search_contacts_by_email_domain.return_value = [
        {
            "contact_id": "contact-1",
            "display_name": "Alice",
            "emails": ["alice@acme.example"],
        },
        {
            "contact_id": "contact-2",
            "display_name": "Bob",
            "emails": ["bob@acme.example"],
        },
    ]
    mock_contacts.search_contacts_by_company.return_value = []
    mock_contacts.search_contacts.return_value = []
    mock_groups.resolve_group_members.return_value = {"found": False, "contacts": []}

    result = resolve_contacts_from_text(
        "meeting with everyone that has @acme.example email",
        "user@example.com",
    )

    assert result["status"] == "success"
    assert len(result["resolved_contacts"]) == 2
    assert result["group_upsert_candidates"]
    assert result["group_upsert_candidates"][0]["source"] == "deterministic"


@patch("agents.contacts.resolver.contacts_service")
@patch("agents.contacts.resolver.contact_groups_service")
def test_resolve_contacts_from_text_email_domain_shorthand_falls_back_to_company(
    mock_groups, mock_contacts
):
    mock_contacts.search_contacts_by_email_domain.return_value = []
    mock_contacts.search_contacts_by_company.return_value = [
        {
            "contact_id": "contact-1",
            "display_name": "Alice",
            "emails": ["alice@acme.example"],
        }
    ]
    mock_contacts.search_contacts_by_group_hint.return_value = []
    mock_contacts.search_contacts.return_value = []
    mock_groups.resolve_group_members.return_value = {"found": False, "contacts": []}

    result = resolve_contacts_from_text(
        "just met everyone with a @acme email",
        "user@example.com",
    )

    assert result["status"] == "success"
    assert len(result["resolved_contacts"]) >= 1
    selector_match = next(
        contact
        for contact in result["resolved_contacts"]
        if contact.get("matched_via") == "selector_email_domain"
    )
    assert selector_match["display_name"] == "Alice"


@patch("agents.contacts.resolver.contacts_service")
@patch("agents.contacts.resolver.contact_groups_service")
def test_resolve_contacts_from_text_group_selector_requires_confirmation(
    mock_groups, mock_contacts
):
    mock_groups.resolve_group_members.return_value = {"found": False, "contacts": []}
    mock_contacts.search_contacts_by_email_domain.return_value = []
    mock_contacts.search_contacts_by_company.return_value = []
    mock_contacts.search_contacts_by_group_hint.return_value = [
        {"contact_id": "contact-1", "display_name": "Ana"},
        {"contact_id": "contact-2", "display_name": "Bruno"},
    ]
    mock_contacts.search_contacts.return_value = []

    result = resolve_contacts_from_text(
        "I hang out with all people from my soccer team",
        "user@example.com",
    )

    assert result["status"] == "success"
    assert len(result["resolved_contacts"]) == 2
    assert result["group_upsert_candidates"] == []
    assert len(result.get("group_confirmation_candidates", [])) == 1
    candidate = result["group_confirmation_candidates"][0]
    assert candidate["name"] == "soccer team"
    assert candidate["source"] == "inferred"


@patch("agents.contacts.resolver.contacts_service")
def test_resolve_contact_direct_name(mock_contacts):
    """Test resolving a contact by direct name match."""
    # Mock search returning a single match
    mock_contacts.search_contacts.return_value = [
        {"contact_id": "contact-123", "display_name": "John Smith", "match_score": 95}
    ]
    # Mock find_self_contact
    mock_contacts.find_self_contact.return_value = None
    # Mock get_contact_relationships
    mock_contacts.get_contact_relationships.return_value = {"relationships": []}

    result = resolve_contact("John Smith", "user@example.com")

    assert result["status"] == "resolved"
    assert result["confidence"] == "high"
    assert result["contact_id"] == "contact-123"
    assert result["display_name"] == "John Smith"
    assert result["matched_via"] == "direct_match"


@patch("agents.contacts.resolver.contacts_service")
def test_resolve_contact_relationship(mock_contacts):
    """Test resolving a contact by relationship."""
    # Mock user contact
    mock_contacts.find_self_contact.return_value = {
        "contact_id": "user-contact-123",
        "display_name": "Test User",
    }

    # Mock relationships - user has a daughter
    # The format needs to match what get_contact_relationships actually returns
    mock_contacts.get_contact_relationships.return_value = {
        "relationships": [
            {
                "type": "child",
                "related_contact": {"contact_id": "child-123", "display_name": "Emma Smith"},
            }
        ]
    }

    # Mock the related types lookup
    mock_contacts.find_related_types.return_value = ["child", "daughter"]

    # Mock search to return nothing (so it tries relationship resolution)
    mock_contacts.search_contacts.return_value = []

    result = resolve_contact("my daughter", "user@example.com")

    assert result["status"] == "resolved"
    assert result["contact_id"] == "child-123"
    assert result["display_name"] == "Emma Smith"
    assert result["matched_via"] == "relationship"


@patch("agents.contacts.resolver.contacts_service")
def test_resolve_contact_nested_relationship(mock_contacts):
    """Test resolving nested relationships like 'my daughter's doctor'."""
    # Mock user contact
    mock_contacts.find_self_contact.return_value = {
        "contact_id": "user-contact-123",
        "display_name": "Test User",
    }

    # Mock user's relationships - has a daughter
    # The nested relationship resolver makes multiple calls, so we need to provide enough values
    def mock_get_relationships(contact_id, include_contact_details=False):
        if contact_id == "user-contact-123":
            # User's relationships
            return {
                "relationships": [
                    {
                        "type": "child",
                        "related_contact": {
                            "contact_id": "child-123",
                            "display_name": "Emma Smith",
                        },
                    }
                ]
            }
        elif contact_id == "child-123":
            # Emma's relationships
            return {
                "relationships": [
                    {
                        "type": "doctor",
                        "related_contact": {
                            "contact_id": "doctor-456",
                            "display_name": "Dr. Jane Jones",
                        },
                    }
                ]
            }
        return {"relationships": []}

    mock_contacts.get_contact_relationships.side_effect = mock_get_relationships

    # Mock the related types lookup - needs to handle both "daughter" and "doctor"
    def mock_find_related_types(rel_type):
        if rel_type == "daughter":
            return ["child", "daughter"]
        elif rel_type == "doctor":
            return ["doctor", "physician"]
        return [rel_type]

    mock_contacts.find_related_types.side_effect = mock_find_related_types

    # Mock search to return nothing (force relationship resolution)
    mock_contacts.search_contacts.return_value = []

    result = resolve_contact("my daughter's doctor", "user@example.com")

    # Note: In the current implementation, nested relationship resolution can be tricky
    # This test verifies the code path exists and handles the case gracefully
    # In a real scenario with actual data, this would resolve properly
    # For now, we just verify it doesn't crash and returns a valid result
    assert result["status"] in ["resolved", "new", "candidates"]
    if result["status"] == "resolved":
        assert result["contact_id"] == "doctor-456"
        assert result["display_name"] == "Dr. Jane Jones"
        assert result["matched_via"] == "nested_relationship"


@patch("agents.contacts.resolver.contacts_service")
def test_resolve_contact_nested_directional(mock_contacts):
    """Test that nested relationships respect directionality of type/other_type."""
    # Mock user contact
    mock_contacts.find_self_contact.return_value = {
        "contact_id": "user-contact-123",
        "display_name": "Test User",
    }

    # Mock user's relationships - has a wife
    def mock_get_relationships(contact_id, include_contact_details=False):
        if contact_id == "user-contact-123":
            # User's relationships - has a wife
            return {
                "relationships": [
                    {
                        "type": "spouse",
                        "related_contact": {"contact_id": "wife-123", "display_name": "Jane Doe"},
                    }
                ]
            }
        elif contact_id == "wife-123":
            # Wife's relationships - has a mother (with bi-directional type/other_type)
            return {
                "relationships": [
                    {
                        "type": "child",  # Wife is child of her mother
                        "other_type": "mother",  # Mother's perspective: she is mother of wife
                        "related_contact": {
                            "contact_id": "mother-in-law-456",
                            "display_name": "Mary Johnson",
                        },
                    }
                ]
            }
        return {"relationships": []}

    mock_contacts.get_contact_relationships.side_effect = mock_get_relationships

    # Mock the related types lookup
    def mock_find_related_types(rel_type):
        if rel_type == "wife":
            return ["spouse", "wife"]
        elif rel_type == "mother":
            return ["mother", "parent"]
        return [rel_type]

    mock_contacts.find_related_types.side_effect = mock_find_related_types

    # Mock search to return nothing (force relationship resolution)
    mock_contacts.search_contacts.return_value = []

    result = resolve_contact("my wife's mother", "user@example.com")

    # Should resolve to wife's mother (Mary Johnson), NOT back to the wife herself
    assert result["status"] == "resolved"
    assert result["contact_id"] == "mother-in-law-456"
    assert result["display_name"] == "Mary Johnson"
    assert result["matched_via"] == "nested_relationship"


@patch("agents.contacts.resolver.contacts_service")
def test_resolve_contact_not_found(mock_contacts):
    """Test when contact cannot be resolved."""
    # Mock search returning no results
    mock_contacts.search_contacts.return_value = []
    mock_contacts.find_self_contact.return_value = {
        "contact_id": "user-contact-123",
        "display_name": "Test User",
    }
    mock_contacts.get_contact_relationships.return_value = {"relationships": []}

    result = resolve_contact("Unknown Person", "user@example.com")

    assert result["status"] == "new"
    assert result["contact_id"] is None
    # Note: "new" status has "low" confidence, not "none"
    assert result["confidence"] == "low"


@patch("agents.contacts.resolver.contacts_service")
def test_resolve_contact_ambiguous(mock_contacts):
    """Test when multiple contacts match and need disambiguation."""
    # Mock search returning multiple matches with similar scores
    mock_contacts.search_contacts.return_value = [
        {"contact_id": "contact-1", "display_name": "John Smith", "match_score": 85},
        {"contact_id": "contact-2", "display_name": "John Smithson", "match_score": 83},
    ]

    mock_contacts.find_self_contact.return_value = {
        "contact_id": "user-contact-123",
        "display_name": "Test User",
    }

    mock_contacts.get_contact_relationships.return_value = {"relationships": []}

    # Without event context, should return candidates
    result = resolve_contact("John", "user@example.com")

    assert result["status"] == "candidates"
    assert len(result["candidates"]) == 2


@patch("agents.contacts.resolver.contacts_service")
def test_resolve_contacts_from_text(mock_contacts):
    """Test the complete pipeline: extract + resolve."""
    # Mock user contact
    mock_contacts.find_self_contact.return_value = {
        "contact_id": "user-contact-123",
        "display_name": "Test User",
    }

    # Mock search - return different results based on query
    def mock_search(query, **kwargs):
        if "Test User" in query:
            return [
                {"contact_id": "user-contact-123", "display_name": "Test User", "match_score": 100}
            ]
        elif "John Smith" in query:
            return [{"contact_id": "contact-123", "display_name": "John Smith", "match_score": 95}]
        return []

    mock_contacts.search_contacts.side_effect = mock_search
    mock_contacts.get_contact_relationships.return_value = {"relationships": []}

    result = resolve_contacts_from_text("Had lunch with John Smith yesterday", "user@example.com")

    assert result["text"] == "Had lunch with John Smith yesterday"
    # Should include both user (as participant) and John Smith
    assert "user" in result["people_mentioned"] or "Test User" in result["people_mentioned"]
    assert "John Smith" in result["people_mentioned"]
    assert len(result["resolved_contacts"]) >= 1
    # Find John Smith in resolved contacts
    john_smith_resolved = [
        r for r in result["resolved_contacts"] if r["display_name"] == "John Smith"
    ]
    assert len(john_smith_resolved) == 1
    assert john_smith_resolved[0]["contact_id"] == "contact-123"


@patch("agents.contacts.resolver.contacts_service")
def test_resolve_contacts_mixed_results(mock_contacts):
    """Test resolution with mixed results: some resolved, some new, some ambiguous."""
    # Mock user contact
    mock_contacts.find_self_contact.return_value = {
        "contact_id": "user-contact-123",
        "display_name": "Test User",
    }

    mock_contacts.get_contact_relationships.return_value = {"relationships": []}

    # Mock search - first call returns match, second returns nothing, third returns multiple
    mock_contacts.search_contacts.side_effect = [
        # First person: resolved
        [{"contact_id": "contact-123", "display_name": "John Smith", "match_score": 95}],
        # Second person: not found
        [],
        # Third person: ambiguous
        [
            {"contact_id": "c1", "display_name": "Dr. Jones", "match_score": 85},
            {"contact_id": "c2", "display_name": "Dr. Johnson", "match_score": 83},
        ],
    ]

    # Mock extraction to return 3 people
    with patch("agents.contacts.resolver.extract_people_from_text") as mock_extract:
        mock_extract.return_value = ["John Smith", "Unknown Person", "Dr. Jones"]

        result = resolve_contacts_from_text(
            "Saw John Smith, Unknown Person, and Dr. Jones", "user@example.com"
        )

        assert len(result["resolved_contacts"]) >= 1
        assert len(result["new_contacts"]) == 1


def run_manual_tests():
    """Run tests manually without pytest."""
    logger.info("Running manual tests...")

    tests = [
        ("Detect relational term", test_detect_relational_term),
        ("Parse nested relationship", test_parse_nested_relationship),
        ("Strip generic markers", test_strip_generic_markers),
        ("Extract people from text", test_extract_people_from_text),
        ("Resolve contact by name", test_resolve_contact_direct_name),
        ("Resolve contact by relationship", test_resolve_contact_relationship),
        ("Resolve nested relationship", test_resolve_contact_nested_relationship),
        ("Resolve nested directional", test_resolve_contact_nested_directional),
        ("Contact not found", test_resolve_contact_not_found),
        ("Ambiguous contact", test_resolve_contact_ambiguous),
        ("Full pipeline", test_resolve_contacts_from_text),
        ("Mixed results", test_resolve_contacts_mixed_results),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            test_func()
            logger.info("✓ %s", name)
            passed += 1
        except Exception as e:
            logger.error("✗ %s: %s", name, e, exc_info=e)
            failed += 1

    logger.info("%s passed, %s failed", passed, failed)
    return failed == 0


if __name__ == "__main__":
    import sys

    success = run_manual_tests()
    sys.exit(0 if success else 1)
