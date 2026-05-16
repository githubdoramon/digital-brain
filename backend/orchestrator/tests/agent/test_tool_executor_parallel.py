"""Tests for dependency-aware batching in tool execution coordinator."""

import os

os.environ.setdefault("LLM_BASE_URL", "http://localhost:11434")
os.environ.setdefault("LLM_CHAT_MODEL_FAST", "test-fast-model")
os.environ.setdefault("LLM_CHAT_MODEL_SMART", "test-smart-model")

from agent.state import AgentState
from agent.tool_executor import ToolExecutionCoordinator


class _DummyController:
    pass


def _call(tool_name: str) -> dict:
    return {
        "id": f"call-{tool_name}",
        "function": {
            "name": tool_name,
            "arguments": "{}",
        },
    }


def test_build_execution_batches_groups_parallel_safe_tools():
    coordinator = ToolExecutionCoordinator(_DummyController())
    batches = coordinator._build_execution_batches(
        [_call("search_memories"), _call("get_document"), _call("home_assistant")]
    )

    assert len(batches) == 2
    assert batches[0][0] is True
    assert len(batches[0][1]) == 2
    assert batches[1][0] is False
    assert batches[1][1][0]["function"]["name"] == "home_assistant"


def test_build_tool_message_payload_preserves_validation_feedback():
    coordinator = ToolExecutionCoordinator(_DummyController())

    payload = coordinator.build_tool_message_payload(
        "get_events",
        {
            "valid": False,
            "tool": "get_events",
            "error": "Parameter 'limit' below minimum (1)",
            "suggestions": ["Remove 'limit' when action='by_ids'"],
        },
    )

    assert payload["valid"] is False
    assert payload["tool"] == "get_events"
    assert "limit" in payload["error"]
    assert payload["suggestions"] == ["Remove 'limit' when action='by_ids'"]


def test_build_tool_message_payload_preserves_execution_errors():
    coordinator = ToolExecutionCoordinator(_DummyController())

    payload = coordinator.build_tool_message_payload(
        "summarize_memories",
        {"error": "time_start and time_end are required", "count": 0},
    )

    assert payload == {"error": "time_start and time_end are required", "count": 0}


def test_build_tool_message_payload_keeps_raw_detail_for_event_inspection():
    coordinator = ToolExecutionCoordinator(_DummyController())

    payload = coordinator.build_tool_message_payload(
        "get_events",
        {
            "events": [
                {
                    "id": "event:1",
                    "title": "Avery <> Alex - 1:1",
                    "summary": "A" * 1200,
                }
            ],
            "count": 1,
        },
        args={"action": "by_ids", "event_ids": ["event:1"]},
        messages=[{"role": "user", "content": "What did Avery say?"}],
    )

    assert payload["events"][0]["summary"] == "A" * 1200
    assert "_meta" not in payload


def test_build_tool_message_payload_compacts_when_budget_is_tight():
    coordinator = ToolExecutionCoordinator(_DummyController())

    payload = coordinator.build_tool_message_payload(
        "get_events",
        {
            "events": [
                {
                    "id": "event:1",
                    "title": "Avery <> Alex - 1:1",
                    "summary": "B" * 30000,
                }
            ],
            "count": 1,
        },
        args={"action": "by_ids", "event_ids": ["event:1"]},
        messages=[{"role": "system", "content": "x" * 100000}],
    )

    assert len(payload["events"][0]["summary"]) < 30000
    assert payload["_meta"]["compacted_for_budget"] is True


def test_repair_reference_ids_recovers_ellipsized_event_id_from_candidates():
    coordinator = ToolExecutionCoordinator(_DummyController())
    state = AgentState(goal="How many weeks is Avery Hill's wife pregnant with?")
    state.remember_information_candidate(
        kind="event",
        candidate_id="google:na1m412fkp6aa9pjgjl5su0kv2_20260406T103000Z:b06712cd",
        label="Avery <> Alex - 1:1",
    )

    repaired = coordinator._repair_reference_ids(
        args={"action": "by_ids", "event_ids": ["google:na1m412fkp6aa9pj..."]},
        state=state,
    )

    assert repaired["event_ids"] == ["google:na1m412fkp6aa9pjgjl5su0kv2_20260406T103000Z:b06712cd"]


def test_repair_reference_ids_recovers_ellipsized_document_id_from_candidates():
    coordinator = ToolExecutionCoordinator(_DummyController())
    state = AgentState(goal="Inspect the lab report")
    state.remember_information_candidate(
        kind="document",
        candidate_id="doc:ea6a1d3a1c4846e9906ff0b361e55529",
        label="Clinical Laboratory Test Results Report",
    )

    repaired = coordinator._repair_reference_ids(
        args={"document_id": "doc:ea6a1d3a1c48..."},
        state=state,
    )

    assert repaired["document_id"] == "doc:ea6a1d3a1c4846e9906ff0b361e55529"


def test_repair_reference_ids_recovers_generic_contact_and_place_ids():
    coordinator = ToolExecutionCoordinator(_DummyController())
    state = AgentState(goal="Who lives at this place?")
    state.remember_information_candidate(
        kind="contact",
        candidate_id="contact:avery-acme-example",
        label="Avery Hill",
    )
    state.remember_information_candidate(
        kind="place",
        candidate_id="place:home-springfield-portugal",
        label="Home",
    )

    repaired = coordinator._repair_reference_ids(
        args={
            "contact_id": "contact:avery-s...",
            "place_id": "place:home-a...",
            "contact_ids": ["contact:avery-s..."],
        },
        state=state,
    )

    assert repaired["contact_id"] == "contact:avery-acme-example"
    assert repaired["place_id"] == "place:home-springfield-portugal"
    assert repaired["contact_ids"] == ["contact:avery-acme-example"]
