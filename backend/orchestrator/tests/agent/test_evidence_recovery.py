"""Regression coverage for mixed personal retrieval and external research."""

from unittest.mock import MagicMock

import pytest

from agent.controller import AgentController
from agent.evidence_requirements import evidence_goal, inspect_requirements, topic_terms
from agent.limits import AgentConfig
from agent.router import IntentRouter, IntentType
from agent.state import AgentState, ToolCallRecord
from agent.tool_visibility_policy import should_escalate_tool_visibility
from tools.handlers.ui import handle_emit_ui_directive
from tools.registry import get_registry
from tools.validators.post_execution import (
    GoalCompletionValidator,
    GoalCoverage,
    PostExecutionValidator,
)
from tools.validators.pre_execution import PreExecutionValidator

GOAL = (
    "For my Beacon meeting today, find public information on all participants to help me prepare."
)
BOUNDS = ("2026-06-01T00:00:00+01:00", "2026-06-01T23:59:59.999999+01:00")
PEOPLE = [
    {"contact_id": f"contact:p{i}", "display_name": name, "emails": [f"person{i}@example.com"]}
    for i, name in enumerate(
        ["Alex Carter", "Dana Lewis", "Morgan Brooks", "Robin Lake", "Taylor Reed"]
    )
]


def record(tool, args=None, result=None, success=True):
    return ToolCallRecord(
        tool_name=tool, arguments=args or {}, result=result or {}, success=success, duration_ms=1
    )


def event_call(title="Beacon planning", date="2026-06-01T10:00:00+01:00", people=None):
    return record(
        "get_events",
        {"action": "by_time_span", "time_start": BOUNDS[0], "time_end": BOUNDS[1]},
        {
            "events": [
                {
                    "id": "event:beacon",
                    "title": title,
                    "start_date": date,
                    "people": PEOPLE if people is None else people,
                }
            ]
        },
    )


def test_help_in_a_retrieval_request_never_short_circuits_to_conversation():
    route = IntentRouter(enable_llm_routing=False)._rule_based_classify(GOAL)
    assert route.intent is not getattr(IntentType, "CONVERSATIONAL", None)
    names = IntentRouter(enable_llm_routing=False).get_allowed_tools(route)
    assert {"get_events", "lookup_contact", "search_memories", "query_graph"} <= set(names)


@pytest.mark.parametrize(
    "question",
    [
        "So?",
        "There are five participants. They are already contacts.",
        "Did you check my calendar?",
    ],
)
def test_followups_preserve_original_request(question):
    task = evidence_goal(
        question,
        [
            {"role": "user", "content": GOAL},
            {"role": "assistant", "content": "I found one company employee."},
        ],
    )
    assert task.startswith(GOAL)
    assert "company employee" not in task


def test_new_task_does_not_inherit_old_meeting():
    assert (
        evidence_goal("Instead, write a greeting", [{"role": "user", "content": GOAL}])
        == "Instead, write a greeting"
    )


@pytest.mark.parametrize(
    "call", [event_call(title="Weekly standup"), event_call(date="2026-05-31T10:00:00+01:00")]
)
def test_successful_but_unrelated_events_do_not_satisfy_request(call):
    result = inspect_requirements(GOAL, [call], bounds=BOUNDS)
    assert result["missing"]
    assert "Identify requested meeting" not in result["completed"]


def test_all_participants_require_identity_tied_research_and_answer_coverage():
    calls = [
        event_call(),
        record("web_search", {"query": "Beacon company team"}, {"results": [{"title": "Team"}]}),
    ]
    assert inspect_requirements(GOAL, calls, bounds=BOUNDS)["missing"]
    calls.extend(
        record("web_search", {"query": person["display_name"]}, {"results": []})
        for person in PEOPLE
    )
    assert not inspect_requirements(GOAL, calls, bounds=BOUNDS)["missing"]
    assert inspect_requirements(
        GOAL, calls, bounds=BOUNDS, answer="Alex Carter has a public profile."
    )["missing"]
    answer = "; ".join(
        person["display_name"] + ": no verified public profile found." for person in PEOPLE
    )
    assert not inspect_requirements(GOAL, calls, bounds=BOUNDS, answer=answer)["missing"]


def test_explicit_attendee_count_cannot_be_satisfied_by_one_contact():
    result = inspect_requirements(
        GOAL + " There are five participants.", [event_call(people=PEOPLE[:1])], bounds=BOUNDS
    )
    assert "smaller" in result["missing"][0]


def test_ambiguous_meetings_require_clarification():
    call = event_call()
    call.result["events"].append({**call.result["events"][0], "id": "event:second"})
    assert (
        "Multiple matching events"
        in inspect_requirements(GOAL, [call], bounds=BOUNDS)["missing"][0]
    )


@pytest.mark.parametrize(
    "params",
    [
        {"query": "Beacon", "limit": "10", "tags": '["Work"]'},
        {"query": "Beacon", "sources": '["knowledge"]'},
    ],
)
def test_read_representation_repairs_are_revalidated(params):
    validation, normalized = PreExecutionValidator(get_registry()).validate_and_normalize(
        "search_memories", params
    )
    assert validation.valid
    if "limit" in params:
        assert normalized["limit"] == 10 and normalized["tags"] == ["Work"]
    else:
        assert normalized["sources"] == ["knowledge"]


@pytest.mark.parametrize(
    "params",
    [
        {"query": "Beacon", "limit": "10.5"},
        {"query": "Beacon", "limit": "0"},
        {"query": "Beacon", "sources": '["unregistered"]'},
        {"query": "Beacon", "max_results": "10"},
    ],
)
def test_repairs_never_bypass_contracts(params):
    validation, normalized = PreExecutionValidator(get_registry()).validate_and_normalize(
        "search_memories", params
    )
    assert not validation.valid and normalized is None
    assert any("schema" in hint for hint in validation.suggestions)


@pytest.mark.asyncio
async def test_validation_failures_are_recorded_for_recovery():
    controller = AgentController(config=AgentConfig(enable_intent_routing=False))
    controller._logger = MagicMock()
    state = AgentState(
        goal="Read a saved document", tool_visibility_mode="restricted", allowed_tool_groups=["web"]
    )
    for i in range(2):
        result = await controller._execute_tool_call(
            {
                "id": f"call-{i}",
                "function": {"name": "get_document", "arguments": {"unknown": True}},
            },
            state,
            state.goal,
            30,
            "test-run",
        )
        assert result["valid"] is False
    assert state.failed_tool_calls == 2 and state.repair_count == 2
    assert should_escalate_tool_visibility(state=state, violation=None)


@pytest.mark.asyncio
async def test_external_research_waits_for_local_participants():
    controller = AgentController(config=AgentConfig(enable_intent_routing=False))
    controller._logger = MagicMock()
    state = AgentState(
        goal=GOAL,
        request_context={
            "timezone": "Europe/Lisbon",
            "temporal_now_iso": "2026-06-01T07:00:00+01:00",
        },
    )
    result = await controller._execute_tool_call(
        {
            "id": "call-web",
            "function": {"name": "web_search", "arguments": {"query": "Beacon company"}},
        },
        state,
        GOAL,
        30,
        "test-run",
    )
    assert result["_prerequisite_blocked"]
    assert state.failed_tool_calls == 1
    assert "time_start=2026-06-01" in result["error"]


def test_validator_suggested_hidden_tools_expand_without_waiting_for_empty_results():
    state = AgentState(
        goal="Read evidence", tool_visibility_mode="restricted", allowed_tool_groups=["web"]
    )
    state.record_tool_call(
        record(
            "web_search",
            result={
                "results": [{"title": "Discovery"}],
                "_validation": {"suggested_tools": ["get_document"]},
            },
        )
    )
    assert should_escalate_tool_visibility(state=state, violation=None)
    state.tool_visibility_escalated = True
    assert not should_escalate_tool_visibility(state=state, violation=None)


def test_info_cards_do_not_finish_work_or_request_input():
    state = AgentState(goal=GOAL)
    result = handle_emit_ui_directive(
        {
            "directive": {
                "version": "1.0",
                "fallback_text": "Finding participants.",
                "blocks": [
                    {
                        "id": "progress",
                        "type": "info_card",
                        "title": "Finding participants",
                        "body": "Checking your meeting.",
                    }
                ],
            }
        },
        state,
    )
    assert result["success"] and not result["needs_user_input"] and not state.pending_questions
    validation = PostExecutionValidator(enable_llm_validation=False).validate(
        "emit_ui_directive", {}, result, GOAL, []
    )
    assert validation.coverage is GoalCoverage.NEEDS_MORE_TOOLS


def test_conversational_route_cannot_waive_explicit_archive_evidence():
    achieved, _, pending = GoalCompletionValidator().check_goal_achieved(
        goal="Find the saved project details",
        tool_calls=[],
        known_facts=[],
        final_content="I have enough context to provide an answer without looking anything up.",
        intent="conversational",
    )
    assert not achieved and pending


def test_compaction_keeps_knowledge_attribution_and_validity():
    controller = AgentController(config=AgentConfig(enable_intent_routing=False))
    row = {
        "kind": "knowledge",
        "id": "k1",
        "statement": "Beacon review was postponed.",
        "inferred": True,
        "status": "historical",
        "valid_until": "2026-05-01",
        "subjects": ["entity:beacon"],
        "evidence": [{"source_ref": "event:one", "quote": "Move the review."}],
    }
    compact = controller.tool_executor._compact_search_memories_result(
        {"results": [row], "count": 1}, aggressive=True
    )
    for key in ("evidence", "subjects", "valid_until", "inferred", "status"):
        assert compact["results"][0][key] == row[key]


def test_lexical_title_query_keeps_distinctive_topic():
    assert topic_terms("Beacon meeting participants today") == ["beacon"]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("memory_first", [False, True])
@pytest.mark.asyncio
async def test_complete_controller_flow_recovers_and_covers_every_participant(
    monkeypatch, stream, memory_first
):
    from agent.router import IntentClassification
    from time_context import use_time_context

    controller = AgentController(
        config=AgentConfig(max_steps=10, max_tool_calls=30, enable_intent_routing=True)
    )
    controller._logger = MagicMock()
    controller._logger.start_run.return_value = "run-fixture"
    controller.post_validator.enable_llm_validation = False

    async def route(*args, **kwargs):
        return IntentClassification(
            intent=IntentType.MEMORY_SEARCH if memory_first else IntentType.WEB_SEARCH,
            confidence=0.99,
            allowed_tool_groups=["memory"] if memory_first else ["web"],
            contact_references=[],
        )

    monkeypatch.setattr(controller, "_run_intent_router", route)
    monkeypatch.setattr(
        controller, "_build_messages", lambda *args, **kwargs: [{"role": "user", "content": GOAL}]
    )
    monkeypatch.setattr(
        controller, "_ensure_contact_scope", lambda *args, **kwargs: ([], [], None, None)
    )
    executed = []

    def handler(tool_name, args, *unused, **kwargs):
        executed.append((tool_name, args))
        if tool_name == "get_events":
            assert args["time_start"] == BOUNDS[0] and args["time_end"] == BOUNDS[1]
            assert args["limit"] == 10
            return event_call().result
        assert tool_name == "web_search"
        assert any(person["display_name"] in args["query"] for person in PEOPLE)
        return {
            "results": [
                {
                    "title": args["query"],
                    "url": "https://example.com/profile",
                    "snippet": "Verified public professional profile.",
                }
            ],
            "count": 1,
        }

    monkeypatch.setattr(controller, "_execute_handler", handler)

    def call(tool, args):
        return {"id": f"call-{tool}-{len(executed)}", "function": {"name": tool, "arguments": args}}

    answer = "; ".join(
        person["display_name"] + ": public professional profile found." for person in PEOPLE
    )
    responses = iter(
        [
            *(
                []
                if memory_first
                else [{"message": {"tool_calls": [call("web_search", {"query": "Beacon team"})]}}]
            ),
            {
                "message": {
                    "tool_calls": [call("get_events", {"action": "by_time_span", "limit": "10"})]
                }
            },
            {
                "message": {
                    "tool_calls": [
                        call("web_search", {"query": person["display_name"]}) for person in PEOPLE
                    ]
                }
            },
            {"message": {"content": "Alex Carter: public professional profile found."}},
            {"message": {"content": answer}},
        ]
    )

    def llm(messages, tools):
        if memory_first:
            names = {tool["function"]["name"] for tool in tools}
            assert ("web_search" in names) == bool(executed)
        assert {"get_events", "lookup_contact", "search_memories"} <= {
            tool["function"]["name"] for tool in tools
        }
        return next(responses)

    monkeypatch.setattr(controller, "_call_llm", llm)

    async def streaming(messages, tools):
        yield llm(messages, tools)

    monkeypatch.setattr(controller, "_stream_llm", streaming)
    with use_time_context({"sent_at": "2026-06-01T07:00:00+01:00", "timezone": "Europe/Lisbon"}):
        if stream:
            items = [
                item
                async for item in controller.run_stream(
                    question=GOAL,
                    user_email="reader@example.com",
                    client_context={"timezone": "Europe/Lisbon"},
                )
            ]
            bundle = next(item["bundle"] for item in items if item["type"] == "done")
        else:
            bundle = await controller.run(
                question=GOAL,
                user_email="reader@example.com",
                client_context={"timezone": "Europe/Lisbon"},
            )
    assert bundle["answer"] == answer
    assert [tool for tool, _ in executed] == ["get_events", *(["web_search"] * 5)]


@pytest.mark.asyncio
async def test_continuation_dates_use_original_timestamp_and_timezone(monkeypatch):
    from time_context import use_time_context

    controller = AgentController(config=AgentConfig(enable_intent_routing=False))
    monkeypatch.setattr(controller, "_build_messages", lambda *a, **k: [])
    original = "[Message sent at 2026-06-01T23:30:00+01:00; timezone: Europe/Lisbon]\n" + GOAL
    state = AgentState(goal="So?", request_context={"timezone": "UTC"})
    with use_time_context({"sent_at": "2026-06-03T09:00:00Z", "timezone": "UTC"}):
        await controller._prepare_execution_context(
            state=state,
            question="So?",
            conversation_history=[
                {
                    "role": "user",
                    "content": original,
                    "sent_at": "2026-06-01T23:30:00+01:00",
                    "timezone": "Europe/Lisbon",
                }
            ],
            user_email="reader@example.com",
            search_limit=30,
            run_id="run-clock",
        )
    assert state.evidence_goal.startswith(GOAL)
    assert state.request_context["temporal_timezone"] == "Europe/Lisbon"
    assert (
        "time_start=2026-06-01T00:00:00+01:00"
        in controller._inspect_evidence_requirements(state)["missing"][0]
    )


def test_web_route_does_not_implicitly_expose_contact_group_mutations():
    from agent.router import IntentClassification

    registry = get_registry()
    router = IntentRouter(enable_llm_routing=False)
    names = router.get_allowed_tools(
        IntentClassification(
            intent=IntentType.WEB_SEARCH, confidence=0.99, allowed_tool_groups=["web"]
        )
    )
    assert "lookup_contact" in names and "select_contacts" not in names
    assert "select_contacts" not in registry.get_tool_names_for_groups(["web"])


@pytest.mark.parametrize(
    "question",
    [
        "What is the weather?",
        "Write a short greeting",
        "For my Zephyr meeting tomorrow, research the participants.",
    ],
)
def test_independent_short_requests_do_not_inherit_a_meeting(question):
    assert evidence_goal(question, [{"role": "user", "content": GOAL}]) == question


def test_long_corrections_do_not_replace_original_research_obligations():
    correction = "There are five participants in today's call, and all of them are already created contacts in our memory."
    task = evidence_goal(
        "So?", [{"role": "user", "content": GOAL}, {"role": "user", "content": correction}]
    )
    assert task.startswith(GOAL)
    assert correction in task
    assert "public" in task and "five participants" in task
