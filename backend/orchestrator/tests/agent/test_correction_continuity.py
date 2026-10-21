"""Correction scope must reach retrieval without unrelated contact blockers."""

import json
from unittest.mock import Mock

import pytest

from agent.contact_scope import ensure_contact_scope
from agent.enums import ToolStatus
from agent.router import IntentRouter, IntentType
from agent.state import AgentState


@pytest.fixture
def history():
    return [
        {
            "role": "user",
            "content": "Recap my call with Alex Carter about Acme last week. I meet Dana Lewis next.",
        },
        {"role": "assistant", "content": "Which Dana Lewis do you mean?"},
        {"role": "user", "content": "Ignore Dana Lewis; focus on the recap."},
        {"role": "assistant", "content": "Which Dana Lewis do you mean?"},
    ]


def route(
    monkeypatch,
    history,
    references,
    question="Stop searching for Dana Lewis. Answer my original question.",
):
    observed = {}

    def classify(messages, **kwargs):
        observed.update(messages=messages, **kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "intent": "memory_search",
                                "confidence": 0.95,
                                "constraints": [],
                                "pre_resolve_contacts": True,
                                "contact_references": references,
                                "should_generate_facts": False,
                                "reasoning": "Continue the requested recap.",
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr("llm_helpers.call_llm_chat", classify)
    result = IntentRouter()._llm_classify(question, history)
    return result, observed


def test_router_receives_original_request_and_untruncated_messages(monkeypatch, history):
    history[0]["content"] += " Additional context." * 60
    result, observed = route(monkeypatch, history, ["Alex Carter"])
    assert observed["messages"][1:-1] == history
    assert observed["context_policy"].recent_dialogue_messages == 12
    assert result.intent is IntentType.MEMORY_SEARCH
    assert result.contact_references == ["Alex Carter"]
    assert result.pre_resolve_contacts


@pytest.mark.parametrize(
    "references,expected",
    [
        (["Dana Lakes", "Fabricated Person"], []),
        (["Which Dana Lewis do you mean?"], []),  # Assistant wording is not user evidence.
        (["Alex Carter", "Alex Carter"], ["Alex Carter"]),
        (["Alex"], ["Alex"]),
        (["Car"], []),  # A substring inside a name is not a reference.
    ],
)
def test_controller_bound_references_must_exist_in_user_text(
    monkeypatch, history, references, expected
):
    result, _ = route(monkeypatch, history, references)
    assert result.contact_references == expected
    assert result.pre_resolve_contacts is bool(expected)


def test_empty_references_do_not_get_reenabled_by_person_memory_heuristics(monkeypatch, history):
    result, _ = route(monkeypatch, history, [], "Ignore Dana Lewis and find my document.")
    assert result.contact_references == []
    assert result.pre_resolve_contacts is False


@pytest.mark.parametrize("require_person_query", [False, True])
def test_automatic_lookup_uses_relevant_person_not_dismissal_text(
    monkeypatch, require_person_query
):
    state = AgentState(goal="Stop searching for Dana Lewis. Answer my original question.")
    state.resolution["request_contact_text"] = "Alex Carter"
    resolve = Mock(return_value={"status": "success"})
    monkeypatch.setattr("agent.contact_scope.resolve_contacts_for_text", resolve)
    ensure_contact_scope(
        state=state,
        text=state.goal,
        user_email="user@example.com",
        conversation_history=[],
        require_person_query=require_person_query,
        normalize_tool_status=lambda *_: ToolStatus.SUCCESS,
        update_state=Mock(),
    )
    assert resolve.call_args.kwargs["text"] == "Alex Carter"


def test_empty_scope_does_not_trigger_on_demand_lookup(monkeypatch):
    state = AgentState(goal="Ignore Dana Lewis and continue.")
    state.resolution["request_contact_text"] = ""
    resolve = Mock()
    monkeypatch.setattr("agent.contact_scope.resolve_contacts_for_text", resolve)
    result = ensure_contact_scope(
        state=state,
        text=state.goal,
        user_email="user@example.com",
        conversation_history=[],
        require_person_query=True,
        normalize_tool_status=Mock(),
        update_state=Mock(),
    )
    resolve.assert_not_called()
    assert result == ([], [], None, False)


def test_needed_identity_ambiguity_still_blocks(monkeypatch):
    state = AgentState(goal="Recap my call with Alex Carter.")
    state.resolution["request_contact_text"] = "Alex Carter"

    def resolve(**kwargs):
        state.resolution["pending_contact_ambiguous_contacts"] = [
            {
                "original_text": "Alex Carter",
                "candidates": [],
            }
        ]
        state.resolution["pending_contact_people"] = ["Alex Carter"]
        return {"status": "need_user_input"}

    monkeypatch.setattr("agent.contact_scope.resolve_contacts_for_text", resolve)
    result = ensure_contact_scope(
        state=state,
        text=state.goal,
        user_email="user@example.com",
        conversation_history=[],
        require_person_query=False,
        normalize_tool_status=lambda *_: ToolStatus.NEED_USER_INPUT,
        update_state=Mock(),
    )
    assert result[2]["status"] == "need_user_input"
    assert "Alex Carter" in result[2]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("references", [["Alex Carter"], []])
async def test_preparation_reaches_answering_context_after_correction(
    monkeypatch, history, references
):
    from agent.controller import AgentController
    from agent.limits import AgentConfig
    from agents.main.agent import build_main_conversational_agent

    result, _ = route(monkeypatch, history, references)
    controller = AgentController(
        config=AgentConfig(),
        conversational_agent=build_main_conversational_agent(
            max_steps=20,
            max_tool_calls=30,
            timeout_seconds=120,
        ),
    )

    async def classify(*_args):
        return result

    def resolve(**kwargs):
        assert kwargs["text"] == "Alex Carter"
        return {"status": "no_people", "people_mentioned": ["Alex Carter"]}

    lookup = Mock(side_effect=resolve)
    monkeypatch.setattr(controller, "_run_intent_router", classify)
    monkeypatch.setattr("agent.contact_scope.resolve_contacts_for_text", lookup)
    monkeypatch.setattr(
        controller,
        "_build_messages",
        lambda question, *args: [{"role": "user", "content": question}],
    )
    state = AgentState(goal="Ignore Dana Lewis and answer the original question.")
    _, clarification, messages, _ = await controller._prepare_execution_context(
        state=state,
        question=state.goal,
        conversation_history=history,
        user_email="user@example.com",
        search_limit=30,
        run_id="correction-test",
    )
    assert clarification is None
    assert messages[-1]["content"] == state.goal
    assert state.resolution["request_contact_text"] == "\n".join(references)
    assert lookup.call_count == bool(references)


def test_selected_names_keep_original_request_constraints_in_resolver_context(monkeypatch):
    from agent.contact_resolution import resolve_contacts_for_text

    state = AgentState(goal="Alex Carter is a new contact, not in my directory.")
    state.resolution["request_contact_text"] = "Alex Carter"
    resolve = Mock(return_value={"status": "no_people"})
    monkeypatch.setattr("contact_resolution_service.resolve_contacts_request", resolve)
    resolve_contacts_for_text(state, "Alex Carter", "user@example.com", [], Mock())
    assert resolve.call_args.args[0]["text"] == "Alex Carter"
    assert resolve.call_args.args[0]["conversation_messages"] == [
        {"role": "user", "content": state.goal},
        {"role": "user", "content": "Alex Carter"},
    ]
