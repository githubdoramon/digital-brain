"""Tests for conservative contact disambiguation policy."""

import sys
import types

from agents.contacts import resolver


def test_llm_disambiguation_requires_context_signal(monkeypatch):
    candidates = [
        {"contact_id": "contact:gio-a", "display_name": "Giovanni Panerai"},
        {"contact_id": "contact:gio-b", "display_name": "Giovanni Ghelfi"},
    ]

    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": candidates,
        },
    )
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": True,
            "contact_id": "contact:gio-a",
            "display_name": "Giovanni Panerai",
            "confidence": "high",
        },
    )

    resolved, new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Gio"],
        user_email="user@example.com",
        full_text="When did I last meet Gio?",
    )

    assert resolved == []
    assert new == []
    assert len(ambiguous) == 1
    assert ambiguous[0]["original_text"] == "Gio"


def test_llm_disambiguation_accepted_when_context_is_specific(monkeypatch):
    candidates = [
        {"contact_id": "contact:gio-a", "display_name": "Giovanni Panerai"},
        {"contact_id": "contact:gio-b", "display_name": "Giovanni Ghelfi"},
    ]

    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": candidates,
        },
    )
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": True,
            "contact_id": "contact:gio-a",
            "display_name": "Giovanni Panerai",
            "confidence": "high",
        },
    )

    resolved, new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Gio"],
        user_email="user@example.com",
        full_text="When did I last meet Gio and we talked about birds?",
    )

    assert len(resolved) == 1
    assert resolved[0]["contact_id"] == "contact:gio-a"
    assert new == []
    assert ambiguous == []


def test_llm_disambiguation_rejected_for_non_high_confidence(monkeypatch):
    candidates = [
        {"contact_id": "contact:gio-a", "display_name": "Giovanni Panerai"},
        {"contact_id": "contact:gio-b", "display_name": "Giovanni Ghelfi"},
    ]

    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": candidates,
        },
    )
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": True,
            "contact_id": "contact:gio-a",
            "display_name": "Giovanni Panerai",
            "confidence": "medium",
        },
    )

    resolved, _new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Gio"],
        user_email="user@example.com",
        full_text="When did I last meet Gio and we talked about birds?",
    )

    assert resolved == []
    assert len(ambiguous) == 1


def test_llm_disambiguation_balanced_accepts_name_level_match(monkeypatch):
    candidates = [
        {"contact_id": "contact:gio-a", "display_name": "Giovanni Panerai"},
        {"contact_id": "contact:gio-b", "display_name": "Giovanni Ghelfi"},
    ]

    monkeypatch.setenv("CONTACT_DISAMBIGUATION_STRICTNESS", "balanced")
    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": candidates,
        },
    )
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": True,
            "contact_id": "contact:gio-a",
            "display_name": "Giovanni Panerai",
            "confidence": "high",
        },
    )

    resolved, _new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Gio"],
        user_email="user@example.com",
        full_text="When did I last meet Gio?",
    )

    assert len(resolved) == 1
    assert resolved[0]["contact_id"] == "contact:gio-a"
    assert ambiguous == []


def test_llm_disambiguation_lenient_accepts_without_context(monkeypatch):
    candidates = [
        {"contact_id": "contact:alice-a", "display_name": "Alice Johnson"},
        {"contact_id": "contact:alice-b", "display_name": "Alice Kim"},
    ]

    monkeypatch.setenv("CONTACT_DISAMBIGUATION_STRICTNESS", "lenient")
    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": candidates,
        },
    )
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": True,
            "contact_id": "contact:alice-a",
            "display_name": "Alice Johnson",
            "confidence": "high",
        },
    )

    resolved, _new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Alice"],
        user_email="user@example.com",
        full_text="When did I meet Alice?",
    )

    assert len(resolved) == 1
    assert resolved[0]["contact_id"] == "contact:alice-a"
    assert ambiguous == []


def test_resolve_people_marks_new_contact_from_llm_flag(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": [
                {"contact_id": "contact:juliana-a", "display_name": "Juliana A"},
                {"contact_id": "contact:juliana-b", "display_name": "Juliana B"},
            ],
        },
    )

    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": False,
            "new_contact": True,
            "contact_id": None,
            "display_name": None,
            "confidence": "high",
        },
    )

    resolved, new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Julia"],
        user_email="user@example.com",
        full_text="Original event description: ...",
        conversation_messages=[
            {"role": "assistant", "content": "I found multiple matching contacts. Please choose."},
            {"role": "user", "content": "It is a new contact, named Julia"},
        ],
    )

    assert resolved == []
    assert ambiguous == []
    assert new == [{"original_text": "Julia", "display_name": "Julia"}]


def test_resolve_contacts_keeps_current_text_even_with_history(monkeypatch):
    captured = {}

    def fake_extract_people(text, conversation_messages=None, **kwargs):
        captured["text"] = text
        captured["conversation_messages"] = conversation_messages
        return []

    monkeypatch.setattr(resolver, "extract_people_from_text", fake_extract_people)

    result = resolver.resolve_contacts_from_text(
        text="Perenai",
        user_email="user@example.com",
        conversation_messages=[
            {"role": "user", "content": "when did I last meet Gio?"},
            {"role": "assistant", "content": "Which Gio did you mean?"},
        ],
    )

    assert captured["text"] == "Perenai"
    assert result["text"] == "Perenai"
    assert result["people_mentioned"] == []


def test_extract_people_ignores_generic_unknown_person_query(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "call_llm_json",
        lambda *_args, **_kwargs: {"people": ["user", "the person"]},
    )

    people = resolver.extract_people_from_text(
        "Who is the person I've met the most in the last 2 weeks?",
    )

    assert people == []


def test_llm_disambiguation_prompt_includes_aliases_and_match_hints(monkeypatch):
    captured = {}

    def fake_call_llm_json(prompt, **_kwargs):
        captured["prompt"] = prompt
        return {
            "decision": "cannot_decide",
            "candidate_number": None,
            "confidence": "low",
            "reasoning": "insufficient context",
        }

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)

    output = resolver._llm_disambiguate_contact(
        person_text="Gio",
        candidates=[
            {
                "contact_id": "contact:gio-acme-xyz",
                "display_name": "Giovanni Panerai",
                "aliases": ["Gio", "Panerai"],
                "match_reason": "exact name match: gio",
            }
        ],
        event_context="Perenai",
    )

    assert output["resolved"] is False
    prompt = captured["prompt"]
    assert "Aliases: Gio, Panerai" in prompt
    assert "Match hint: exact name match: gio" in prompt


def test_llm_disambiguation_prompt_includes_chronological_history(monkeypatch):
    captured = {}

    def fake_call_llm_json(prompt, **_kwargs):
        captured["prompt"] = prompt
        return {
            "decision": "cannot_decide",
            "candidate_number": None,
            "new_contact": True,
            "confidence": "low",
            "reasoning": "user says none of these",
        }

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)

    resolver._llm_disambiguate_contact(
        person_text="Julia",
        candidates=[
            {
                "contact_id": "contact:juliana-charles-c2092f",
                "display_name": "Juliana Charles",
                "aliases": [],
                "match_reason": "name contains: juliana charles",
            }
        ],
        event_context="on February 8th I went with my daughter to her friend's birthday, Julia",
        conversation_messages=[
            {"role": "assistant", "content": "I found multiple matching contacts. Please choose."},
            {"role": "user", "content": "None of these. It is a new contact, named Julia"},
        ],
    )

    prompt = captured["prompt"]
    assert "Disambiguation history (chronological, oldest first):" in prompt
    assert "- assistant: I found multiple matching contacts. Please choose." in prompt
    assert "- user: None of these. It is a new contact, named Julia" in prompt
    assert "Treat the latest user message as the clarification answer" in prompt
    assert '"new_contact": true or false' in prompt


def test_resolve_contact_uses_any_search_for_role_queries(monkeypatch):
    captured = {}

    monkeypatch.setattr(resolver.contacts_service, "find_self_contact", lambda *_a, **_k: None)
    monkeypatch.setattr(
        resolver.contacts_service,
        "search_contacts",
        lambda query, **kwargs: captured.update({"query": query, "kwargs": kwargs}) or [],
    )

    result = resolver.resolve_contact("the CTO of Acme", "user@example.com")

    assert result["status"] == "new"
    assert captured["query"] == "CTO of Acme"
    assert captured["kwargs"]["search_by"] == "any"


def test_resolve_contacts_short_circuits_simple_relationship_query(monkeypatch):
    llm_calls = []

    monkeypatch.setattr(
        resolver.contacts_service,
        "find_self_contact",
        lambda *_a, **_k: {"contact_id": "user-1", "display_name": "Test User"},
    )
    monkeypatch.setattr(
        resolver.contacts_service,
        "get_contact_relationships",
        lambda *_a, **_k: {
            "relationships": [
                {
                    "contact_id": "contact-daughter",
                    "related_contact": {
                        "contact_id": "contact-daughter",
                        "display_name": "Emma",
                    },
                    "type": "parent",
                    "other_type": "daughter",
                }
            ]
        },
    )
    monkeypatch.setattr(
        resolver,
        "call_llm_json",
        lambda *args, **kwargs: llm_calls.append((args, kwargs)) or {"people": []},
    )

    result = resolver.resolve_contacts_from_text(
        "When did I last meet my daughter?",
        "user@example.com",
        mode=resolver.MINIMAL_RESOLUTION_MODE,
    )

    assert result["status"] == "success"
    assert any(item["display_name"] == "Emma" for item in result["resolved_contacts"])
    assert llm_calls == []


def test_minimal_mode_skips_enrichment_steps(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "extract_people_from_text",
        lambda *_args, **_kwargs: ["John Smith"],
    )
    monkeypatch.setattr(
        resolver,
        "_resolve_people_mentions",
        lambda *args, **kwargs: (
            [
                {
                    "original_text": "John Smith",
                    "contact_id": "contact-1",
                    "display_name": "John Smith",
                    "matched_via": "direct_match",
                    "confidence": "high",
                    "resolution_path": None,
                }
            ],
            [],
            [],
            {},
        ),
    )

    def fail_professions(*_args, **_kwargs):
        raise AssertionError("profession inference should be skipped in minimal mode")

    def fail_relationships(*_args, **_kwargs):
        raise AssertionError("relationship inference should be skipped in minimal mode")

    monkeypatch.setattr(resolver, "_infer_professions_for_new_contacts", fail_professions)
    monkeypatch.setattr(resolver, "_infer_relationship_pairs", fail_relationships)

    result = resolver.resolve_contacts_from_text(
        "John Smith",
        "user@example.com",
        mode=resolver.MINIMAL_RESOLUTION_MODE,
    )

    assert result["status"] == "success"
    assert result["suggested_relationships"] == []


def test_suggest_missing_relationships_prefetches_relationship_graph(monkeypatch):
    relationship_calls = []

    monkeypatch.setattr(
        resolver.contacts_service,
        "find_self_contact",
        lambda *_a, **_k: {"contact_id": "user-1", "display_name": "User"},
    )

    def fake_get_contact_relationships(contact_id, include_contact_details=False):
        relationship_calls.append((contact_id, include_contact_details))
        return {
            "found": True,
            "relationships": [
                {"contact_id": "contact-2"} if contact_id == "contact-1" else {"contact_id": "contact-9"}
            ],
        }

    monkeypatch.setattr(
        resolver.contacts_service,
        "get_contact_relationships",
        fake_get_contact_relationships,
    )
    monkeypatch.setattr(
        resolver,
        "_infer_relationship_types",
        lambda *_args, **_kwargs: {"type": "doctor", "other_type": "patient"},
    )
    monkeypatch.setattr(resolver, "_infer_profession_from_text", lambda *_args, **_kwargs: None)

    suggestions = resolver._suggest_missing_relationships(
        pairs=[
            {"person_text": "Alice", "anchor_text": "Bob", "relationship_hint": "doctor"},
            {"person_text": "Alice", "anchor_text": "Cara", "relationship_hint": "doctor"},
        ],
        full_text="Alice is Bob's doctor and Cara's doctor",
        user_email="user@example.com",
        resolution_cache={
            "Alice": {"status": "resolved", "contact_id": "contact-1", "display_name": "Alice"},
            "Bob": {"status": "resolved", "contact_id": "contact-2", "display_name": "Bob"},
            "Cara": {"status": "resolved", "contact_id": "contact-3", "display_name": "Cara"},
        },
        profession_by_text={},
    )

    assert len(suggestions) == 1
    assert sorted(relationship_calls) == [
        ("contact-1", False),
        ("contact-2", False),
        ("contact-3", False),
    ]


def test_extract_people_splits_selector_prompt(monkeypatch):
    prompts = []

    def fake_call_llm_json(prompt, **_kwargs):
        prompts.append(prompt)
        prompt_lower = prompt.lower()
        if "extract collective participant selectors" in prompt_lower:
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
        return {"people": ["user", "John Smith"]}

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)

    people, selectors = resolver.extract_people_from_text(
        "I met John Smith and everyone with @acme.example",
        include_collective_selectors=True,
    )

    assert people == ["user", "John Smith"]
    assert any(
        isinstance(selector, dict) and selector.get("kind") == "email_domain"
        for selector in selectors
    )
    assert len(prompts) == 2
    assert "extract all person references" in prompts[0].lower()
    assert "collective group selectors" not in prompts[0].lower()
    assert "extract collective participant selectors" in prompts[1].lower()


def test_nested_relationship_reuses_prior_resolved_full_name(monkeypatch):
    monkeypatch.setattr(
        resolver.contacts_service,
        "get_contact_relationships",
        lambda contact_id, include_contact_details=False: {
            "relationships": [
                {
                    "type": "sibling",
                    "other_type": "family",
                    "related_contact": {
                        "contact_id": "contact-mariela",
                        "display_name": "Iris Lewis",
                    },
                }
            ]
            if contact_id == "contact-dana"
            else []
        },
    )

    nested = resolver._resolve_nested_relationship(
        ["Dana", "family"],
        "user@example.com",
        resolution_cache={
            "Dana Lewis": {
                "status": "resolved",
                "contact_id": "contact-dana",
                "display_name": "Dana Lewis",
                "matched_via": "direct_match",
                "confidence": "high",
            }
        },
    )

    assert nested["found"] is True
    assert nested["contact_id"] == "contact-mariela"
    assert nested["path"] == ["user", "Dana Lewis", "Iris Lewis"]


def test_relationship_candidates_include_match_reason():
    relationship_context = {
        "relationships": [
            {
                "type": "doctor",
                "related_contact": {"contact_id": "doc-1", "display_name": "Dr. One"},
            },
            {
                "type": "doctor",
                "related_contact": {"contact_id": "doc-2", "display_name": "Dr. Two"},
            },
        ]
    }

    result = resolver._resolve_via_relationship("doctor", relationship_context)

    assert result["found"] is False
    assert len(result["candidates"]) == 2
    assert all(c.get("match_reason") == "relationship match: doctor" for c in result["candidates"])


def test_resolve_contact_nested_collective_uses_related_candidates(monkeypatch):
    monkeypatch.setattr(resolver.contacts_service, "find_self_contact", lambda *_a, **_k: None)

    def fake_search_contacts(query, **kwargs):
        if query == "Dana Lewis":
            return [
                {
                    "contact_id": "contact:dana",
                    "display_name": "Dana Lewis",
                    "match_score": 100,
                }
            ]
        return []

    monkeypatch.setattr(resolver.contacts_service, "search_contacts", fake_search_contacts)

    monkeypatch.setattr(
        resolver.contacts_service,
        "get_contact_relationships",
        lambda contact_id, **_kwargs: {
            "relationships": [
                {
                    "contact_id": "contact:robin",
                    "type": "parent",
                    "other_type": "daughter",
                    "related_contact": {"display_name": "Robin Lake"},
                },
                {
                    "contact_id": "contact:jamie",
                    "type": "parent",
                    "other_type": "daughter",
                    "related_contact": {"display_name": "Jamie Lake"},
                },
                {
                    "contact_id": "contact:rafael",
                    "type": "spouse",
                    "other_type": "spouse",
                    "related_contact": {"display_name": "Hugo Lake"},
                },
            ]
        }
        if contact_id == "contact:dana"
        else {"relationships": []},
    )

    monkeypatch.setattr(
        resolver,
        "call_llm_json",
        lambda *_args, **_kwargs: {
            "candidate_numbers": [1, 2, 3],
            "collective_reference": True,
            "confidence": "high",
            "reasoning": "whole family refers to multiple close relatives",
        },
    )

    result = resolver.resolve_contact("Dana Lewis's whole family", "user@example.com")

    assert result["status"] == "candidates"
    assert result["auto_resolve_candidates"] is True
    assert "needs_clarification" not in result
    assert len(result["candidates"]) == 3
    assert all(c.get("contact_id", "").startswith("contact:") for c in result["candidates"])


def test_resolve_contact_my_whole_family_anchors_on_user(monkeypatch):
    monkeypatch.setattr(
        resolver.contacts_service,
        "find_self_contact",
        lambda *_a, **_k: {"contact_id": "contact:user", "display_name": "Ramon"},
    )
    monkeypatch.setattr(
        resolver.contacts_service,
        "search_contacts",
        lambda *_a, **_k: [],
    )

    monkeypatch.setattr(
        resolver.contacts_service,
        "get_contact_relationships",
        lambda contact_id, **_kwargs: {
            "relationships": [
                {
                    "contact_id": "contact:robin",
                    "type": "parent",
                    "other_type": "daughter",
                    "related_contact": {"display_name": "Robin Lake"},
                },
                {
                    "contact_id": "contact:jamie",
                    "type": "parent",
                    "other_type": "daughter",
                    "related_contact": {"display_name": "Jamie Lake"},
                },
            ]
        }
        if contact_id == "contact:user"
        else {"relationships": []},
    )

    monkeypatch.setattr(
        resolver,
        "call_llm_json",
        lambda *_args, **_kwargs: {
            "candidate_numbers": [1, 2],
            "collective_reference": True,
            "confidence": "high",
            "reasoning": "whole family implies multiple relatives",
        },
    )

    result = resolver.resolve_contact("my whole family", "user@example.com")

    assert result["status"] == "candidates"
    assert result["auto_resolve_candidates"] is True
    assert "needs_clarification" not in result
    assert {item["contact_id"] for item in result["candidates"]} == {
        "contact:robin",
        "contact:jamie",
    }


def test_resolve_people_mentions_auto_resolves_collective_candidates(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": [
                {"contact_id": "contact:robin", "display_name": "Robin Lake"},
                {"contact_id": "contact:jamie", "display_name": "Jamie Lake"},
            ],
            "confidence": "high",
            "auto_resolve_candidates": True,
            "skip_auto_disambiguation": True,
        },
    )

    resolved, new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Dana Lewis's whole family"],
        user_email="user@example.com",
        full_text="having lunch with Dana Lewis and his whole family",
    )

    assert len(resolved) == 2
    assert {item["contact_id"] for item in resolved} == {"contact:robin", "contact:jamie"}
    assert all(item["matched_via"] == "nested_relationship_group" for item in resolved)
    assert new == []
    assert ambiguous == []


def test_resolve_contact_applies_hard_rule_before_disambiguation(monkeypatch):
    monkeypatch.setattr(
        resolver.contacts_service,
        "find_self_contact",
        lambda *_args, **_kwargs: None,
    )

    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LLM should not be called")),
    )

    facts_module = types.SimpleNamespace(
        get_hard_rules_for_scope=lambda *_args, **_kwargs: [
            {
                "fact_id": "uf_rule",
                "rule_type": "entity_alias",
                "rule_payload": {
                    "alias_text": "Dana",
                    "target_text": "Dana Lewis",
                },
            }
        ]
    )
    monkeypatch.setitem(sys.modules, "user_facts", facts_module)

    monkeypatch.setattr(
        resolver.contacts_service,
        "search_contacts",
        lambda query, **_kwargs: [
            {
                "contact_id": "contact:dana-lewis",
                "display_name": "Dana Lewis",
                "aliases": ["Dana"],
                "match_score": 100,
            }
        ]
        if query == "Dana Lewis"
        else [],
    )

    result = resolver.resolve_contact("Dana", "user@example.com", event_context="with Dana")

    assert result["status"] == "resolved"
    assert result["matched_via"] == "hard_rule"
    assert result["contact_id"] == "contact:dana-lewis"


def test_contact_resolution_timeout_override_replaces_inner_timeout(monkeypatch):
    captured: dict[str, object] = {}

    def fake_call_llm_json(_prompt, **kwargs):
        captured.update(kwargs)
        return {"people": [], "collective_selectors": []}

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)

    with resolver.use_contact_resolution_timeout(120):
        resolver._call_contact_resolution_llm_json("prompt", timeout=60, use_fast_model=True)

    assert captured["timeout"] == 120


def test_nested_relationship_fallback_uses_top_level_contact_id(monkeypatch):
    monkeypatch.setattr(
        resolver.contacts_service,
        "get_contact_relationships",
        lambda *_args, **_kwargs: {
            "relationships": [
                {
                    "contact_id": "contact:family-member",
                    "related_contact": {"display_name": "Family Member"},
                }
            ]
        },
    )
    monkeypatch.setattr(
        resolver,
        "_resolve_via_relationship",
        lambda *_args, **_kwargs: {
            "found": False,
            "contact_id": None,
            "display_name": None,
            "confidence": "low",
            "candidates": [],
        },
    )
    monkeypatch.setattr(
        resolver.contacts_service,
        "search_contacts",
        lambda *_args, **_kwargs: [
            {"contact_id": "contact:family-member", "display_name": "Family Member"}
        ],
    )

    result = resolver._resolve_nested_relationship(
        ["Dana", "family"],
        "user@example.com",
        resolution_cache={
            "Dana": {
                "status": "resolved",
                "contact_id": "contact:dana-lewis",
                "display_name": "Dana Lewis",
            }
        },
    )

    assert result["found"] is True
    assert result["contact_id"] == "contact:family-member"
    assert result["display_name"] == "Family Member"
