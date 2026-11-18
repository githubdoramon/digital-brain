"""Regression coverage for bounded recaps and context overflow recovery."""

from unittest.mock import Mock

import pytest

from agent.controller import AgentController
from agent.evidence_requirements import evidence_goal, recap_candidates
from agent.guardrails import extract_explicit_time_bounds
from agent.limits import AgentConfig
from agent.router import IntentClassification, IntentType
from agent.state import AgentState, ToolCallRecord
from llm_policy import LLMRequestRejected

NOW = "2026-06-12T11:00:00+00:00"
QUESTION = "I have a new call with Beacon/Harbor today. Summarize the last call so I can prepare."
PAST = {
    "id": "event:demo",
    "title": "Beacon assignment interview",
    "start_date": "2026-06-03T11:00:00+00:00",
    "summary": "Calendar invite for the assignment presentation.",
}
TODAY = {
    "id": "event:today",
    "title": "Beacon final interview",
    "start_date": "2026-06-12T15:00:00+00:00",
}
UNRELATED = {"id": "event:unrelated", "title": "Weekly standup", "start_date": PAST["start_date"]}
NOTES = {
    "id": "doc:demo",
    "kind": "document",
    "title": "Beacon assignment interview notes",
    "snippet": "The group agreed to test the onboarding checklist with three teams.",
}


def test_month_without_a_day_does_not_inherit_request_day():
    for phrase in ("The interview was in May.", "Notes from May 2026."):
        assert extract_explicit_time_bounds(phrase, reference_time_iso=NOW) is None


def test_search_date_is_specific_to_the_tool_query():
    controller = AgentController(config=AgentConfig(enable_intent_routing=False))
    question = "The follow-up is today. The earlier interview was in May."
    state = AgentState(goal=question, request_context={"temporal_now_iso": NOW})
    args = {"query": "Beacon assignment interview May 23 2026"}
    normalized, _ = controller._prepare_memory_search_arguments(args, state, question, None, [])
    assert normalized["time_start"] == "2026-05-23T00:00:00+00:00"
    args["time_start"] = "2026-05-20"
    normalized, _ = controller._prepare_memory_search_arguments(args, state, question, None, [])
    assert normalized["time_start"] == "2026-05-20"
    assert "time_end" not in normalized


def test_recap_date_ignores_today_in_an_unrelated_clause():
    controller = AgentController(config=AgentConfig(enable_intent_routing=False))
    question = "I have a call with Beacon today. Summarize the last interview on June 3."
    state = AgentState(goal=question, request_context={"temporal_now_iso": NOW})
    normalized, _ = controller._prepare_get_events_arguments(
        {"action": "by_time_span"},
        state,
        question,
        None,
        [],
    )
    assert normalized["time_start"] == "2026-06-03T00:00:00+00:00"
    assert normalized["time_end"] == "2026-06-03T23:59:59.999999+00:00"


def test_clarification_reply_selects_one_of_the_retrieved_meetings():
    other = {**PAST, "id": "event:other", "title": "Beacon technical interview"}
    history = [
        {"role": "user", "content": QUESTION},
        {
            "role": "assistant",
            "content": "Which meeting should I summarize? Reply with its title or date.",
        },
    ]
    tool = ToolCallRecord("get_events", {}, {"events": [PAST, other]}, 1.0, True)
    goal = evidence_goal(other["title"], history)
    assert goal.startswith(QUESTION)
    assert recap_candidates(goal, [tool], now=NOW) == [other]
    assert (
        recap_candidates(
            evidence_goal(other["title"], history),
            [ToolCallRecord("get_events", {}, {"events": [PAST]}, 1.0, True)],
            now=NOW,
        )
        == []
    )


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("scenario", ["notes", "no_notes", "ambiguous", "overflow"])
@pytest.mark.asyncio
async def test_run_reaches_a_bounded_answer_or_clarification(monkeypatch, stream, scenario):
    from time_context import use_time_context

    controller = AgentController(config=AgentConfig(enable_intent_routing=True))
    controller.post_validator.enable_llm_validation = False
    controller._logger = Mock()
    controller._logger.start_run.return_value = "run-fixture"

    async def route(*args, **kwargs):
        return IntentClassification(
            intent=IntentType.MEMORY_SEARCH,
            confidence=0.99,
            allowed_tool_groups=["memory"],
            contact_references=[],
        )

    monkeypatch.setattr(controller, "_run_intent_router", route)
    monkeypatch.setattr(
        controller, "_build_messages", lambda *args: [{"role": "user", "content": QUESTION}]
    )
    executed = []
    model_calls = []
    question = QUESTION if scenario != "overflow" else "Find the Beacon launch notes."
    initial_tool = "search_memories" if scenario == "overflow" else "get_events"

    def handler(tool_name, args, *unused, **kwargs):
        executed.append(tool_name)
        if tool_name == "get_events":
            events = [UNRELATED, TODAY, PAST]
            if scenario == "ambiguous":
                events.append({**PAST, "id": "event:other", "title": "Beacon technical interview"})
            return {"events": events, "count": len(events)}
        assert tool_name == "search_memories"
        if scenario != "overflow":
            assert args["query"] == PAST["title"]
            assert args["limit"] == 6
            assert args["time_start"].startswith("2026-06-02")
            assert args["time_end"].startswith("2026-06-04")
        results = [] if scenario == "no_notes" else [NOTES, NOTES]
        return {"results": results, "count": len(results)}

    def model(messages, tools):
        model_calls.append(tools)
        if len(model_calls) > 1:
            assert scenario == "overflow"
            raise LLMRequestRejected("context_length_exceeded")
        args = (
            {"action": "by_time_span", "time_start": "2026-06-01", "time_end": "2026-06-13"}
            if initial_tool == "get_events"
            else {"query": "Beacon launch notes"}
        )
        return {
            "message": {
                "tool_calls": [
                    {"id": "call-1", "function": {"name": initial_tool, "arguments": args}}
                ]
            }
        }

    async def stream_model(messages, tools):
        yield {**model(messages, tools), "done": True}

    answer = (
        "I found the Beacon assignment interview, but couldn’t verify what was discussed."
        if scenario == "no_notes"
        else "The notes say the group agreed to test the onboarding checklist with three teams."
    )
    syntheses = []

    def synthesize(messages, tools, **kwargs):
        syntheses.append(messages)
        assert tools == [] and kwargs["max_tokens"] == 2000
        assert question in messages[1]["content"]
        evidence = messages[-1]["content"]
        assert "event:unrelated" not in evidence and "event:today" not in evidence
        assert evidence.count("doc:demo") == (0 if scenario == "no_notes" else 1)
        if scenario != "overflow":
            assert "event:demo" in evidence and "agenda" in messages[0]["content"]
        return {"message": {"content": answer}}

    monkeypatch.setattr(controller, "_execute_handler", handler)
    monkeypatch.setattr(controller, "_call_llm", model)
    monkeypatch.setattr(controller, "_stream_llm", stream_model)
    monkeypatch.setattr("agent.controller.call_llm_with_tools", synthesize)

    with use_time_context({"sent_at": NOW, "timezone": "UTC"}):
        if stream:
            events = [event async for event in controller.run_stream(question)]
            assert events[-1]["type"] == "done", events[-1]
            bundle = events[-1]["bundle"]
        else:
            bundle = await controller.run(question)

    if scenario == "ambiguous":
        assert executed == ["get_events"] and not syntheses
        assert "Which meeting should I summarize?" in bundle["answer"]
        assert (
            PAST["title"] in bundle["answer"] and "Beacon technical interview" in bundle["answer"]
        )
        assert UNRELATED["title"] not in bundle["answer"]
    else:
        assert len(syntheses) == 1 and bundle["answer"] == answer
        assert executed == (
            ["search_memories"] if scenario == "overflow" else ["get_events", "search_memories"]
        )
