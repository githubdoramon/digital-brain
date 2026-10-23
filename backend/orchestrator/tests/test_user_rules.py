# ruff: noqa: F811
"""Explicit instructions remain scoped and private through the knowledge transition."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import knowledge_jobs
import user_rules
from prompts.context import get_user_context
from routes import rules as routes
from tests.test_intelligence import OTHER, OWNER, database  # noqa: F401


@pytest.fixture
def rules_database(database, monkeypatch):
    monkeypatch.setattr(user_rules, "get_conn", database)
    return database


def save_rule(owner=OWNER, **changes):
    return user_rules.upsert_rule(
        owner,
        "Interpret Dana as Dana Lewis.",
        rule_type="entity_alias",
        scope=["contact_resolution"],
        payload={"alias_text": "Dana", "target_text": "Dana Lewis"},
        **changes,
    )


def test_saved_rules_keep_ids_owners_scopes_and_payloads(rules_database):
    first = save_rule(rule_id="saved-rule")
    again = save_rule(rule_id=first["rule_id"])
    assert again["rule_id"] == "saved-rule"
    assert user_rules.list_rules(OTHER) == []
    rows = user_rules.get_rules_for_scope(OWNER, scope="contact_resolution")
    assert len(rows) == 1 and rows[0]["payload"]["target_text"] == "Dana Lewis"
    assert user_rules.get_rules_for_scope(OWNER, scope="event_command") == []
    assert "Dana Lewis" in user_rules.get_rules_context(OWNER, scope="contact_resolution")
    assert user_rules.get_rules_for_scope(OWNER, scope="invalid") == []


def test_global_rules_apply_in_other_scopes_and_soft_records_are_inaccessible(rules_database):
    with rules_database() as conn:
        conn.execute(
            "INSERT INTO user_facts(fact_id,user_email,content,category,importance) VALUES('soft',%s,'Prefers quiet keyboards','preference',5)",
            (OWNER,),
        )
    user_rules.upsert_rule(
        OWNER,
        "Interpret Dana as Dana Lewis.",
        rule_type="entity_alias",
        scope=["agent_global"],
        payload={"alias_text": "Dana", "target_text": "Dana Lewis"},
    )
    assert len(user_rules.list_rules(OWNER)) == 1
    assert len(user_rules.get_rules_for_scope(OWNER, scope="event_command")) == 1
    assert not user_rules.delete_rule(OWNER, "soft")
    assert not user_rules.delete_rule(OTHER, user_rules.list_rules(OWNER)[0]["rule_id"])


def test_upsert_cannot_overwrite_another_owners_rule_or_a_legacy_fact(rules_database):
    save_rule(rule_id="fixed")
    with pytest.raises(ValueError, match="unavailable"):
        save_rule(OTHER, rule_id="fixed")
    with rules_database() as conn:
        conn.execute(
            "INSERT INTO user_facts(fact_id,user_email,content) VALUES('soft',%s,'Existing historical evidence')",
            (OWNER,),
        )
    with pytest.raises(ValueError, match="unavailable"):
        save_rule(rule_id="soft")


@pytest.mark.parametrize("scope", [[], ["invalid"], ["contact_resolution", "invalid"]])
def test_invalid_instruction_scopes_are_rejected(scope):
    with pytest.raises(ValueError, match="scope"):
        user_rules.upsert_rule(
            OWNER,
            "Interpret Dana as Dana Lewis.",
            rule_type="entity_alias",
            scope=scope,
            payload={"alias_text": "Dana", "target_text": "Dana Lewis"},
        )


@pytest.mark.parametrize(
    "text", ["Whenever I say Dana, I mean Dana Lewis.", "Dana means Dana Lewis."]
)
def test_explicit_aliases_use_the_same_parser_and_controlled_payload(text):
    rows = user_rules.extract_explicit_rules(text)
    assert len(rows) == 1 and rows[0]["type"] == "entity_alias"
    assert rows[0]["payload"] == {"alias_text": "Dana", "target_text": "Dana Lewis"}
    assert user_rules.extract_explicit_rules("I prefer quiet keyboards.") == []


def test_chat_callback_only_captures_explicit_rules_and_replays_keep_one_id(rules_database):
    for _ in range(2):
        knowledge_jobs.capture_explicit_rules(
            user_email=OWNER,
            user_message="Whenever I say Dana, I mean Dana Lewis.",
            assistant_message="Assistant text must not become evidence.",
            thread_id="thread",
        )
    rows = user_rules.list_rules(OWNER)
    assert len(rows) == 1 and rows[0]["source_thread_id"] == "thread"
    knowledge_jobs.capture_explicit_rules(
        user_email=OWNER, user_message="I prefer quiet keyboards."
    )
    assert len(user_rules.list_rules(OWNER)) == 1


def test_new_rules_api_is_private_and_deletion_keeps_knowledge_separate(rules_database):
    rule = save_rule()
    app = FastAPI()
    app.include_router(routes.create_rules_router())
    app.dependency_overrides[routes.get_current_user] = lambda: {"email": OTHER}
    with TestClient(app) as client:
        response = client.get("/mobile/rules")
        assert response.json() == {"rules": []} and response.headers["cache-control"] == "no-store"
        assert client.delete("/mobile/rules/" + rule["rule_id"]).status_code == 404
        app.dependency_overrides[routes.get_current_user] = lambda: {"email": OWNER}
        row = client.get("/mobile/rules").json()["rules"][0]
        assert row["rule_id"] == rule["rule_id"] and "fact_id" not in row
        assert client.delete("/mobile/rules/" + row["rule_id"]).status_code == 204
        assert client.get("/mobile/rules").json() == {"rules": []}


def test_obsolete_fact_and_duplicate_knowledge_routes_are_removed():
    from routes.intelligence import create_intelligence_router
    from routes.knowledge import create_knowledge_router
    from routes.user import create_user_router

    app = FastAPI()
    for factory in [
        create_user_router,
        create_intelligence_router,
        create_knowledge_router,
        routes.create_rules_router,
    ]:
        app.include_router(factory())
    # Included routers may be lazy wrappers; inspect the resolved public API.
    paths = set(app.openapi()["paths"])
    assert "/user/facts" not in paths and "/mobile/user/facts" not in paths
    assert "/user/facts/{fact_id}" not in paths and "/mobile/user/facts/{fact_id}" not in paths
    assert (
        "/mobile/intelligence/knowledge" not in paths
        and "/mobile/intelligence/knowledge/{knowledge_id}" not in paths
    )
    assert "/mobile/knowledge" in paths and "/mobile/rules" in paths


@patch(
    "user_rules.get_rules_context",
    return_value="Deterministic user rules: interpret Dana as Dana Lewis.",
)
@patch(
    "knowledge.list_items",
    return_value=[{"statement": "Likes concise answers", "inferred": True, "status": "current"}],
)
def test_prompt_context_keeps_explicit_rules_separate_from_attributed_knowledge(
    mock_facts, mock_rules
):
    result = get_user_context(OWNER, "schedule meeting", scope=user_rules.RuleScope.EVENT_COMMAND)
    assert "Deterministic user rules" in result and "Relevant archive knowledge" in result
    assert '"inferred": true' in result
    mock_rules.assert_called_once_with(OWNER, scope=user_rules.RuleScope.EVENT_COMMAND)
