"""
Tests for the user_facts service and fact_extraction pipeline.

These tests mock DB and LLM calls to verify logic without external dependencies.
"""

from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# Ensure the orchestrator package is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from user_fact_rules import RuleScope

# ---------------------------------------------------------------------------
# user_facts service tests
# ---------------------------------------------------------------------------


class TestUserFactsRowToDict:
    """Test the _row_to_dict helper."""

    def test_strips_embed_columns(self):
        import user_facts

        row = {
            "fact_id": "uf_abc",
            "user_email": "u@test.com",
            "content": "Likes jazz",
            "category": "preference",
            "importance": 7,
            "source_thread_id": None,
            "access_count": 0,
            "last_accessed_at": None,
            "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "content_embed": [0.1, 0.2],
            "content_tsv": "some tsvector",
            "semantic_score": 0.9,
        }
        result = user_facts._row_to_dict(row)
        assert "content_embed" not in result
        assert "content_tsv" not in result
        assert "semantic_score" not in result
        assert result["fact_id"] == "uf_abc"
        assert result["content"] == "Likes jazz"

    def test_serialises_datetimes(self):
        import user_facts

        now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        row = {
            "fact_id": "uf_1",
            "user_email": "u@test.com",
            "content": "Test",
            "category": "general",
            "importance": 5,
            "source_thread_id": None,
            "access_count": 0,
            "last_accessed_at": now,
            "created_at": now,
            "updated_at": now,
        }
        result = user_facts._row_to_dict(row)
        assert isinstance(result["created_at"], str)
        assert "2025-06-15" in result["created_at"]


class TestUserFactsValidation:
    """Test category and importance validation in upsert_fact."""

    @patch("user_facts.get_conn")
    @patch("user_facts._generate_embedding", return_value=[0.0] * 768)
    def test_invalid_category_defaults_to_general(self, mock_embed, mock_conn):
        import user_facts

        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {
            "fact_id": "uf_1",
            "user_email": "u@test.com",
            "content": "Test",
            "category": "general",
            "importance": 5,
            "source_thread_id": None,
            "access_count": 0,
            "last_accessed_at": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        mock_conn.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__ = MagicMock(
            return_value=mock_cur
        )
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__exit__ = MagicMock(
            return_value=False
        )
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        user_facts.upsert_fact("u@test.com", "Test", category="INVALID")
        # Verify the SQL was called with "general" not "INVALID"
        call_args = mock_cur.execute.call_args
        params = call_args[0][1]
        assert params[3] == "general"  # category param position

    def test_importance_clamping(self):
        assert max(1, min(10, 0)) == 1
        assert max(1, min(10, 15)) == 10
        assert max(1, min(10, 7)) == 7


class TestRetrievalScoring:
    """Test the hybrid scoring formula."""

    def test_recency_decay(self):
        import user_facts

        # Recent fact (1 hour ago) should have high recency
        recent_decay = math.exp(-user_facts.RECENCY_DECAY_RATE * 1)
        assert recent_decay > 0.99

        # Old fact (720 hours = 30 days ago) should have lower recency
        old_decay = math.exp(-user_facts.RECENCY_DECAY_RATE * 720)
        assert old_decay < 0.5

        # Very old fact (2160 hours = 90 days) should be very low
        very_old_decay = math.exp(-user_facts.RECENCY_DECAY_RATE * 2160)
        assert very_old_decay < 0.15

    def test_scoring_weights_sum_to_one(self):
        import user_facts

        total = (
            user_facts.WEIGHT_SEMANTIC + user_facts.WEIGHT_IMPORTANCE + user_facts.WEIGHT_RECENCY
        )
        assert abs(total - 1.0) < 0.001


class TestGetFactsForContext:
    """Test the context formatting function."""

    @patch("user_facts.search_user_facts")
    @patch("user_facts.record_fact_access")
    def test_formats_facts_as_lines(self, mock_access, mock_search):
        import user_facts

        mock_search.return_value = [
            {"fact_id": "f1", "content": "Likes rock music", "category": "preference"},
            {"fact_id": "f2", "content": "Software engineer", "category": "biographical"},
        ]

        result = user_facts.get_facts_for_context("u@test.com", "concerts near me")

        assert result is not None
        assert "- [preference] Likes rock music" in result
        assert "- [biographical] Software engineer" in result
        mock_access.assert_called_once_with(["f1", "f2"])

    @patch("user_facts.search_user_facts")
    def test_returns_none_when_no_facts(self, mock_search):
        import user_facts

        mock_search.return_value = []
        result = user_facts.get_facts_for_context("u@test.com", "test")
        assert result is None


class TestHardRules:
    @patch("user_facts.get_hard_rules_for_scope")
    def test_formats_entity_alias_rule_context(self, mock_rules):
        import user_facts

        mock_rules.return_value = [
            {
                "fact_id": "f_rule",
                "rule_type": "entity_alias",
                "rule_payload": {
                    "alias_text": "Dana",
                    "target_text": "Dana Lewis",
                },
                "content": "fallback",
            }
        ]

        context = user_facts.get_hard_rules_context("u@test.com", scope=RuleScope.CONTACT_RESOLUTION)
        assert context is not None
        assert "Deterministic user rules" in context
        assert "Dana Lewis" in context

    def test_upsert_hard_rule_requires_non_empty_scope(self):
        import user_facts

        try:
            user_facts.upsert_fact(
                "u@test.com",
                "If user says 'Dana', resolve as 'Dana Lewis'.",
                category="behavioral",
                importance=9,
                fact_mode="hard_rule",
                rule_type="entity_alias",
                rule_scope=[],
                rule_payload={"alias_text": "Dana", "target_text": "Dana Lewis"},
            )
            raise AssertionError("Expected ValueError")
        except ValueError as exc:
            assert "rule_scope" in str(exc)

    def test_upsert_hard_rule_rejects_invalid_scope_value(self):
        import user_facts

        try:
            user_facts.upsert_fact(
                "u@test.com",
                "If user says 'Dana', resolve as 'Dana Lewis'.",
                category="behavioral",
                importance=9,
                fact_mode="hard_rule",
                rule_type="entity_alias",
                rule_scope=["not_a_scope"],
                rule_payload={"alias_text": "Dana", "target_text": "Dana Lewis"},
            )
            raise AssertionError("Expected ValueError")
        except ValueError as exc:
            assert "Invalid rule_scope" in str(exc)


# ---------------------------------------------------------------------------
# fact_extraction tests
# ---------------------------------------------------------------------------


class TestExtractionGating:
    """Test the heuristic gate in maybe_extract_facts."""

    @patch("fact_extraction._run_extraction")
    def test_skips_short_messages(self, mock_run):
        from fact_extraction import maybe_extract_facts

        maybe_extract_facts(
            user_email="u@test.com",
            user_message="hi",
            assistant_message="Hello!",
        )
        mock_run.assert_not_called()

    @patch("fact_extraction._run_extraction")
    def test_skips_command_messages(self, mock_run):
        from fact_extraction import maybe_extract_facts

        maybe_extract_facts(
            user_email="u@test.com",
            user_message="/new",
            assistant_message="Session reset",
        )
        mock_run.assert_not_called()

    @patch("fact_extraction._run_extraction")
    def test_runs_for_substantial_messages(self, mock_run):
        from fact_extraction import maybe_extract_facts

        maybe_extract_facts(
            user_email="u@test.com",
            user_message="I really enjoy listening to rock music when I work",
            assistant_message="That's great! Rock music can help with focus.",
            thread_id="thread_123",
        )
        mock_run.assert_called_once()

    @patch("fact_extraction._run_extraction", side_effect=RuntimeError("boom"))
    def test_exception_does_not_propagate(self, mock_run):
        """Background extraction must never crash the caller."""
        from fact_extraction import maybe_extract_facts

        # Should not raise
        maybe_extract_facts(
            user_email="u@test.com",
            user_message="I really enjoy listening to rock music when I work",
            assistant_message="That's great!",
        )

    @patch("llm_helpers.call_llm_json")
    @patch("fact_extraction._get_contacts_summary", return_value="")
    @patch("user_facts.get_user_facts", return_value=[])
    def test_extraction_uses_smart_model_with_high_effort(
        self,
        _mock_get_facts,
        _mock_contacts_summary,
        mock_call_llm_json,
    ):
        from fact_extraction import _run_extraction

        mock_call_llm_json.return_value = {"facts": []}

        _run_extraction(
            user_email="u@test.com",
            user_message="I really enjoy listening to rock music when I work",
            assistant_message="That's great!",
            thread_id="thread_123",
        )

        assert mock_call_llm_json.call_args.kwargs["use_fast_model"] is False
        assert mock_call_llm_json.call_args.kwargs["reasoning_effort"] == "high"


class TestExtractionPromptBuilding:
    """Test the prompt construction."""

    def test_includes_existing_facts(self):
        from fact_extraction import _build_extraction_prompt

        existing = [
            {"fact_id": "f1", "content": "Likes jazz", "category": "preference", "importance": 6}
        ]
        prompt = _build_extraction_prompt(
            user_message="I've been getting into rock lately",
            assistant_message="Nice!",
            existing_facts=existing,
            contacts_summary="  - Maria (wife)",
        )
        assert 'id="f1"' in prompt
        assert "Likes jazz" in prompt
        assert "Maria (wife)" in prompt
        assert "I've been getting into rock lately" in prompt

    def test_handles_empty_existing(self):
        from fact_extraction import _build_extraction_prompt

        prompt = _build_extraction_prompt(
            user_message="I love cooking",
            assistant_message="What do you like to cook?",
            existing_facts=[],
            contacts_summary="",
        )
        assert "(none yet)" in prompt
        assert "I love cooking" in prompt


class TestReconciliation:
    """Test the fact reconciliation logic."""

    @patch("user_facts.upsert_fact")
    def test_add_action(self, mock_upsert):
        from fact_extraction import _reconcile_fact

        mock_upsert.return_value = {"fact_id": "uf_new"}

        action = _reconcile_fact(
            {"action": "ADD", "content": "Likes rock", "category": "preference", "importance": 7},
            user_email="u@test.com",
            thread_id="t1",
        )

        assert action == "ADD"
        mock_upsert.assert_called_once_with(
            "u@test.com",
            "Likes rock",
            category="preference",
            importance=7,
            fact_mode="soft",
            rule_type=None,
            rule_scope=[],
            rule_payload={},
            source_thread_id="t1",
        )

    @patch("user_facts.update_fact")
    def test_update_action(self, mock_update):
        from fact_extraction import _reconcile_fact

        mock_update.return_value = {"fact_id": "f1"}

        action = _reconcile_fact(
            {
                "action": "UPDATE",
                "content": "Prefers rock over jazz",
                "category": "preference",
                "importance": 8,
                "target_fact_id": "f1",
            },
            user_email="u@test.com",
            thread_id="t1",
        )

        assert action == "UPDATE"
        mock_update.assert_called_once_with(
            "f1",
            content="Prefers rock over jazz",
            category="preference",
            importance=8,
            fact_mode="soft",
            rule_type=None,
            rule_scope=[],
            rule_payload={},
        )

    @patch("user_facts.delete_fact")
    def test_delete_action(self, mock_delete):
        from fact_extraction import _reconcile_fact

        mock_delete.return_value = True

        action = _reconcile_fact(
            {"action": "DELETE", "target_fact_id": "f1"},
            user_email="u@test.com",
            thread_id="t1",
        )

        assert action == "DELETE"
        mock_delete.assert_called_once_with("f1")

    def test_noop_action(self):
        from fact_extraction import _reconcile_fact

        action = _reconcile_fact(
            {"action": "NOOP"},
            user_email="u@test.com",
            thread_id="t1",
        )
        assert action == "NOOP"

    @patch("user_facts.upsert_fact")
    def test_update_without_target_falls_back_to_add(self, mock_upsert):
        from fact_extraction import _reconcile_fact

        mock_upsert.return_value = {"fact_id": "uf_new"}

        action = _reconcile_fact(
            {"action": "UPDATE", "content": "New fact", "category": "general", "importance": 5},
            user_email="u@test.com",
            thread_id="t1",
        )

        assert action == "ADD"
        mock_upsert.assert_called_once()

    def test_add_with_empty_content_is_noop(self):
        from fact_extraction import _reconcile_fact

        action = _reconcile_fact(
            {"action": "ADD", "content": "", "category": "general", "importance": 5},
            user_email="u@test.com",
            thread_id="t1",
        )
        assert action == "NOOP"

    @patch("user_facts.upsert_fact")
    def test_hard_rule_add_passes_mode_and_payload(self, mock_upsert):
        from fact_extraction import _reconcile_fact

        mock_upsert.return_value = {"fact_id": "uf_rule"}

        action = _reconcile_fact(
            {
                "action": "ADD",
                "content": "",
                "category": "behavioral",
                "importance": 9,
                "fact_mode": "hard_rule",
                "rule_type": "entity_alias",
                "rule_scope": [RuleScope.CONTACT_RESOLUTION.value, RuleScope.AGENT_GLOBAL.value],
                "rule_payload": {
                    "alias_text": "Dana",
                    "target_text": "Dana Lewis",
                },
            },
            user_email="u@test.com",
            thread_id="t1",
        )

        assert action == "ADD"
        kwargs = mock_upsert.call_args.kwargs
        assert kwargs["fact_mode"] == "hard_rule"
        assert kwargs["rule_type"] == "entity_alias"
        assert kwargs["rule_payload"]["target_text"] == "Dana Lewis"


class TestContactsSummary:
    """Test the contacts summary builder."""

    @patch("contacts.list_contacts")
    def test_builds_summary(self, mock_list):
        mock_list.return_value = [
            {
                "display_name": "Maria",
                "relationships": [{"relationship_type": "wife"}],
            },
            {
                "display_name": "John",
                "relationships": [],
            },
        ]
        from fact_extraction import _get_contacts_summary

        result = _get_contacts_summary("u@test.com")
        assert "Maria (wife)" in result
        assert "John" in result

    @patch("contacts.list_contacts", side_effect=Exception("db error"))
    def test_handles_errors_gracefully(self, mock_list):
        from fact_extraction import _get_contacts_summary

        result = _get_contacts_summary("u@test.com")
        assert result == ""


# ---------------------------------------------------------------------------
# Context injection tests
# ---------------------------------------------------------------------------


class TestUserFactsContext:
    """Test the prompt context injection function."""

    @patch("user_facts.get_facts_for_context")
    def test_returns_formatted_context(self, mock_get):
        mock_get.return_value = "- [preference] Likes rock\n- [biographical] Engineer"

        from prompts.context import get_user_facts_context

        result = get_user_facts_context("u@test.com", "find concerts")
        assert result is not None
        assert "Known facts about this user:" in result
        assert "Likes rock" in result

    @patch("user_facts.get_facts_for_context", return_value=None)
    def test_returns_none_when_no_facts(self, mock_get):
        from prompts.context import get_user_facts_context

        result = get_user_facts_context("u@test.com", "test")
        assert result is None

    def test_returns_none_for_empty_email(self):
        from prompts.context import get_user_facts_context

        result = get_user_facts_context("", "test")
        assert result is None

    @patch("user_facts.get_hard_rules_context")
    @patch("user_facts.get_facts_for_context")
    def test_merges_hard_rules_and_soft_facts(self, mock_facts, mock_hard):
        mock_hard.return_value = (
            "Deterministic user rules (apply before disambiguation):\n"
            "- If the user says 'Dana', interpret it as 'Dana Lewis'."
        )
        mock_facts.return_value = "- [preference] Likes concise answers"

        from prompts.context import get_user_facts_context

        result = get_user_facts_context(
            "u@test.com", "schedule meeting", scope=RuleScope.EVENT_COMMAND
        )
        assert result is not None
        assert "Deterministic user rules" in result
        assert "Known facts about this user" in result


class TestExplicitHardRuleExtraction:
    def test_extracts_alias_rule_pattern(self):
        from fact_extraction import _extract_explicit_hard_rules

        rules = _extract_explicit_hard_rules("Whenever I say Dana, I mean Dana Lewis.")

        assert len(rules) == 1
        payload = rules[0]["rule_payload"]
        assert payload["alias_text"] == "Dana"
        assert payload["target_text"] == "Dana Lewis"
