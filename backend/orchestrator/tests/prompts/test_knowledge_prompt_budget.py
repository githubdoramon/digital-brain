import json
from datetime import datetime, timezone
from unittest.mock import Mock

from llm_context import estimate_input
from prompts.context import get_knowledge_context, get_user_context


def test_knowledge_excerpts_are_bounded_and_keep_whole_facts(monkeypatch):
    rows = [
        {
            "knowledge_id": f"knowledge:{i}",
            "statement": ("Prefers quiet rooms. " * 12).strip(),
            "subject_ref": "contact:alex",
            "subjects": ["contact:alex"],
            "predicate": "preference",
            "status": "uncertain",
            "inferred": True,
            "inference_reason": "Indirect evidence only.",
            "valid_until": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "user_email": "alex@example.com",
            "revision": 900,
            "evidence": [{"quote": "A long source quotation. " * 2000}],
            "embedding": [0.1] * 2000,
        }
        for i in range(12)
    ]
    monkeypatch.setattr("knowledge.list_items", lambda *a, **k: rows)
    rendered = get_knowledge_context("alex@example.com", "room preferences")
    assert estimate_input({"messages": [{"role": "system", "content": rendered}]}) <= 1400
    excerpts = json.loads(rendered.split("\n", 1)[1])
    assert 0 < len(excerpts) < len(rows)
    assert all(e["statement"] == rows[0]["statement"] for e in excerpts)
    assert all(e["status"] == "uncertain" and e["inferred"] for e in excerpts)
    assert all(e["inference_reason"] == "Indirect evidence only." for e in excerpts)
    assert all(e["valid_until"].startswith("2026-01-01") for e in excerpts)
    assert all(e["subjects"] == ["contact:alex"] for e in excerpts)
    assert "quote" not in rendered and "embedding" not in rendered and "user_email" not in rendered
    assert "bounded selection" in rendered


def test_oversized_statement_is_omitted_whole_not_cut_mid_fact(monkeypatch):
    monkeypatch.setattr(
        "knowledge.list_items",
        lambda *a, **k: [
            {"knowledge_id": "large", "statement": "A detailed observation. " * 3000},
            {"knowledge_id": "small", "statement": "Prefers blue."},
        ],
    )
    result = get_knowledge_context("alex@example.com", "preferences")
    assert "large" not in result and "Prefers blue." in result


def test_explicit_rules_are_not_subject_to_fact_budget(monkeypatch):
    rules = "An explicit user constraint. " * 1000
    monkeypatch.setattr("user_rules.get_rules_context", lambda *a, **k: rules)
    knowledge = Mock(side_effect=AssertionError("archive lookup"))
    monkeypatch.setattr("prompts.context.get_knowledge_context", knowledge)
    assert get_user_context("alex@example.com", "Hello", include_knowledge=False) == rules
    knowledge.assert_not_called()
