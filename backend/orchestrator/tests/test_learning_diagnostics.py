from __future__ import annotations

import logging
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

import intelligence
import intelligence_context
import intelligence_model as model
import knowledge
import llm_helpers
from learning_validation import LearningValidationError
from observability.diagnostics import diagnostic, diagnostic_scope


@pytest.fixture(autouse=True)
def detailed_logging(monkeypatch, caplog):
    monkeypatch.setenv("LLM_DIAGNOSTICS_ENABLED", "true")
    caplog.set_level(logging.INFO)


def source():
    return {
        "ref": "source-1:0",
        "source_id": 1,
        "part": 0,
        "kind": "chat",
        "entity_id": "message-1",
        "text": "Alex Carter returned the Slate laptop yesterday.",
        "payload": {},
        "actionable": False,
    }


def test_span_and_exact_excerpt_are_accepted_together():
    row = source()
    result = {
        "findings": [
            {
                "evidence": [
                    {"ref": row["ref"], "span_index": 0, "quote": "returned the Slate laptop"}
                ]
            }
        ]
    }
    expanded = model.expand_citations(result, [row])
    evidence = intelligence.evidence_for(expanded["findings"][0], [row])
    assert evidence[0]["quote"] == "returned the Slate laptop"


def test_quote_cannot_belong_to_a_different_span():
    row = {**source(), "text": "x" * 1000 + "The second span contains the Slate laptop."}
    with pytest.raises(model.InvalidModelOutput) as error:
        model.expand_citations(
            {
                "findings": [
                    {
                        "evidence": [
                            {"ref": row["ref"], "span_index": 0, "quote": "the Slate laptop"}
                        ]
                    }
                ]
            },
            [row],
        )
    assert error.value.details["reason"] == "quote_not_in_selected_span"
    assert error.value.details["expected_span"] == "x" * 1000


@pytest.mark.parametrize("changed", [False, True])
def test_reconciliation_keeps_resolved_people_and_new_entity_bindings(monkeypatch, changed):
    row = source()
    context = {name: [] for name in intelligence_context.FIELDS}
    context["contacts"] = [{"contact_id": "contact-1", "display_name": "Alex Carter"}]
    original = {"ref": "new:laptop", "kind": "device", "label": "Slate laptop"}
    candidate = {
        "statement": "The laptop was returned.",
        "subject_ref": "new:laptop",
        "related_subjects": ["contact:contact-1"],
        "entities": [original],
        "evidence": [{"ref": row["ref"], "span_index": 0, "quote": None}],
    }
    result = {
        **deepcopy(candidate),
        "entities": [{**original, "kind": "product"}] if changed else [],
    }
    monkeypatch.setattr(
        intelligence,
        "context_for",
        lambda *args: {name: [] for name in intelligence_context.FIELDS},
    )

    def ask(payload, **kwargs):
        assert payload["context"]["contacts"] == context["contacts"]
        return {"findings": [result], "deferred_refs": []}

    monkeypatch.setattr(model, "_ask", ask)
    payload = {
        "sources": [{"ref": row["ref"], "text_spans": [row["text"]]}],
        "primary_refs": [row["ref"]],
    }
    if changed:
        with pytest.raises(model.InvalidModelOutput) as error:
            model._reconcile("learner@example.com", [row], payload, [candidate], context)
        assert error.value.details["reason"] == "entity_binding_changed"
    else:
        reconciled = model._reconcile("learner@example.com", [row], payload, [candidate], context)
        finding = reconciled["findings"][0]
        assert finding["entities"] == [original]
        expanded = model.expand_citations(reconciled, [row])["findings"][0]
        validated = knowledge.validate_entities(
            expanded, context, intelligence.evidence_for(expanded, [row])
        )
        assert validated["subject_ref"].startswith("entity:")
        assert validated["related_subjects"] == ["contact:contact-1"]


def test_unused_entity_definitions_are_not_saved_or_allowed_to_block_a_valid_fact():
    finding = {
        "subject_ref": "new:laptop",
        "entities": [
            {"ref": "new:laptop", "kind": "device", "label": "Slate laptop"},
            {"ref": "new:other", "kind": "organization", "label": "Unrelated Acme"},
        ],
    }
    validated = knowledge.validate_entities(finding, {}, [{"quote": source()["text"]}])
    assert [entity["label"] for entity in validated["entity_records"]] == ["Slate laptop"]


def test_unknown_subject_logs_actual_candidate_and_gives_targeted_repair_feedback(
    monkeypatch, caplog
):
    candidate = {"subject_ref": "contact:missing", "statement": "Alex returned a laptop."}
    with pytest.raises(LearningValidationError) as rejected:
        knowledge.validate_entities(candidate, {}, [{"quote": source()["text"]}])
    calls = []

    def analyze(*args, repair_hint=None, **kwargs):
        calls.append(repair_hint)
        if repair_hint is None:
            raise rejected.value
        assert repair_hint["details"]["rejected_refs"] == ["contact:missing"]
        assert repair_hint["details"]["allowed_refs"] == ["general", "self"]
        return {}, []

    monkeypatch.setattr(model, "_analyze", analyze)
    assert model._analyze_repaired("learner@example.com", [source()]) == ({}, [])
    assert len(calls) == 2
    assert "Alex returned a laptop" in caplog.text
    assert "contact:missing" in caplog.text
    assert str(rejected.value) == "invalid_subject"


def test_diagnostics_show_local_content_and_scope_but_redact_configured_credentials(
    monkeypatch, caplog
):
    monkeypatch.setenv("LLM_API_KEY", "test-model-secret")
    with diagnostic_scope(analysis_id="analysis-1", owner="learner@example.com"):
        diagnostic(
            "validation",
            statement="Alex returned the Slate laptop.",
            api_key="test-model-secret",
            response="Bearer test-model-secret",
            nested={"password": "private-password"},
        )
    assert "Alex returned the Slate laptop" in caplog.text
    assert "analysis-1" in caplog.text
    assert "test-model-secret" not in caplog.text
    assert "private-password" not in caplog.text
    caplog.clear()
    diagnostic("next_job", source="source-2")
    assert "analysis-1" not in caplog.text


def test_diagnostics_can_be_disabled(monkeypatch, caplog):
    monkeypatch.setenv("LLM_DIAGNOSTICS_ENABLED", "false")
    diagnostic("validation", statement="PRIVATE_SOURCE")
    assert "PRIVATE_SOURCE" not in caplog.text


def test_sensitive_llm_calls_now_show_local_input_output_and_provider_errors(monkeypatch, caplog):
    monkeypatch.setenv("LLM_BASE_URL", "https://models.example/v1")
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [{"message": {"content": '{"answer":"Slate laptop"}'}}]
    }
    monkeypatch.setattr(llm_helpers.requests, "post", lambda *args, **kwargs: response)
    assert llm_helpers.call_llm_json(
        "Inspect the source about Alex Carter.", model="smart-test", sensitive_payload=True
    ) == {"answer": "Slate laptop"}
    assert "Inspect the source about Alex Carter" in caplog.text
    assert "Slate laptop" in caplog.text
    assert "llm_request" in caplog.text and "llm_response" in caplog.text
