"""Conversation must not acquire retrieval prerequisites from incidental words."""

from unittest.mock import AsyncMock, Mock

import pytest

from agent.controller import AgentController
from agent.limits import AgentConfig
from agent.planning_policy import build_execution_plan, verify_final_response
from agent.response_guardrails import looks_like_continuation
from agent.router import IntentRouter, IntentType
from agent.state import AgentState, ToolCallRecord
from agent.tool_visibility_policy import resolve_tool_visibility
from agents.main import message_builder
from agents.main.agent import build_main_conversational_agent
from llm_context import estimate_input
from tools.registry import get_registry
from tools.validators.post_execution import GoalCompletionValidator


@pytest.mark.parametrize("intent", [None, "unknown", "conversational"])
@pytest.mark.parametrize(
    "user_request,answer",
    [
        ("Tell me a joke", "Why did the bicycle fall over? It was two tired."),
        (
            "What is photosynthesis?",
            "Plants use sunlight to make sugars from water and carbon dioxide.",
        ),
        ("How do rainbows form?", "Water droplets refract and reflect sunlight."),
        ("Create a short poem", "Rain falls.\nThe garden wakes."),
        ("Write a greeting", "Hello!"),
        ("What is two plus two?", "Four."),
        ("Thanks!", "You're welcome."),
        ("What's photosynthesis?", "Plants use sunlight to make sugars."),
        ("Find a rhyme for rain", "Train."),
        ("How can I improve my writing?", "Read widely and revise each draft."),
        ("Create a story about my imaginary dragon", "Your dragon woke beneath a silver moon."),
        ("Send me a joke", "Why did the bicycle fall over? It was two tired."),
        ("Open with a joke", "Why did the bicycle fall over? It was two tired."),
        ("Explain how to find saved records", "Search the archive using a relevant title or date."),
    ],
)
def test_direct_answers_do_not_require_tool_calls(user_request, answer, intent):
    achieved, reason, pending = GoalCompletionValidator().check_goal_achieved(
        goal=user_request,
        tool_calls=[],
        known_facts=[],
        final_content=answer,
        intent=intent,
    )
    assert achieved and not pending
    assert verify_final_response(
        final_content=answer,
        goal_check={"achieved": achieved, "reason": reason},
        completion_evidence=[],
        tool_calls_count=0,
    )[0]


@pytest.mark.parametrize(
    "user_request,intent",
    [
        ("Tell me about my appointments", "memory_search"),
        ("What is my latest lab reading?", None),
        ("Find the saved project details", "conversational"),
        ("Turn off the lights", "home_control"),
        ("Create a task", None),
        ("Write a file", "unknown"),
    ],
)
def test_real_retrieval_and_execution_still_need_evidence(user_request, intent):
    achieved, _, pending = GoalCompletionValidator().check_goal_achieved(
        goal=user_request,
        tool_calls=[],
        known_facts=[],
        final_content="I completed the request successfully.",
        intent=intent,
    )
    assert not achieved and pending


def test_empty_results_can_be_reported_without_another_search():
    call = ToolCallRecord("search_memories", {}, {"results": [], "count": 0}, 1, True)
    achieved, _, pending = GoalCompletionValidator().check_goal_achieved(
        goal="Find the saved project details",
        tool_calls=[call],
        known_facts=[],
        final_content="I couldn't find matching saved project details.",
        intent="memory_search",
    )
    assert achieved and not pending


@pytest.mark.parametrize(
    "text",
    [
        "I should have brought an umbrella. That's the punchline!",
        "The character says, 'let me search', then opens a dictionary.",
        "I need to practice more.",
    ],
)
def test_ordinary_sentences_are_not_tool_promises(text):
    assert not looks_like_continuation(text)


def test_actual_unfulfilled_tool_promise_is_still_detected():
    assert looks_like_continuation("Let me search your stored notes.")


def conversation_route():
    return IntentRouter(enable_llm_routing=False)._parse_llm_response(
        '{"intent":"conversational","confidence":0.95,"pre_resolve_contacts":false}'
    )


def test_conversation_route_has_no_catalogue_or_obligatory_ui_call():
    route = conversation_route()
    assert route.intent is IntentType.CONVERSATIONAL
    tools, _, groups = resolve_tool_visibility(
        tool_registry=get_registry(),
        classification=route,
        restriction_mode="conservative",
        high_threshold=0.8,
        medium_threshold=0.6,
    )
    assert tools == groups == []
    assert "tools" not in " ".join(build_execution_plan("Tell me a joke", route.intent)).lower()


@pytest.mark.parametrize(
    "question",
    [
        "Tell me a joke about lights",
        "How do I turn on a lamp?",
        "Explain what a shell is",
        "How many legs does a spider have?",
        "What is an average?",
        "What happened to dinosaurs?",
        "Write a script for a short play",
    ],
)
def test_incidental_subject_words_do_not_force_tool_routes(question):
    route = IntentRouter(enable_llm_routing=False)._rule_based_classify(question)
    assert route.intent is IntentType.UNKNOWN


@pytest.mark.asyncio
async def test_standalone_joke_skips_router_model_but_mixed_request_does_not(monkeypatch):
    router = IntentRouter(llm_base_url="https://models.example/v1", llm_model="test-model")
    model = Mock(return_value=conversation_route())
    monkeypatch.setattr(router, "_llm_classify", model)
    assert (
        await router.classify("Please tell me a short joke!")
    ).intent is IntentType.CONVERSATIONAL
    model.assert_not_called()
    await router.classify("Tell me a joke and find the saved project notes")
    model.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "user_request,answer",
    [
        ("What is two plus two?", "Four."),
        ("Tell me a joke", "I should have brought an umbrella. That's the punchline!"),
        ("Show a JSON object with a name", '{"name":"Alex"}'),
    ],
)
async def test_controller_finishes_conversation_in_one_model_round(
    monkeypatch, user_request, answer, streaming
):
    agent = build_main_conversational_agent(max_steps=8, max_tool_calls=10, timeout_seconds=30)
    controller = AgentController(config=AgentConfig(), conversational_agent=agent)
    monkeypatch.setattr(
        controller, "_run_intent_router", AsyncMock(return_value=conversation_route())
    )
    monkeypatch.setattr(
        message_builder, "_cached_tag_context", Mock(side_effect=AssertionError("taxonomy lookup"))
    )
    monkeypatch.setattr(
        message_builder, "inject_main_skills", Mock(side_effect=AssertionError("skill lookup"))
    )
    model = Mock(return_value={"message": {"content": answer}})
    monkeypatch.setattr(controller, "_call_llm", model)

    async def stream(messages, tools):
        yield {**model(messages, tools), "done": True}

    monkeypatch.setattr(controller, "_stream_llm", stream)
    monkeypatch.setattr(
        controller,
        "_finalize",
        lambda question, content, state, *args: {
            "answer": content,
            "rounds": state.step_count,
            "achieved": state.goal_achieved,
        },
    )
    if streaming:
        events = [event async for event in controller.run_stream(user_request)]
        result = next(event["bundle"] for event in events if event["type"] == "done")
    else:
        result = await controller.run(user_request)
    assert result == {"answer": answer, "rounds": 1, "achieved": True}
    model.assert_called_once()
    assert model.call_args.args[1] == []
    assert estimate_input({"messages": model.call_args.args[0]}) < 1600


def test_conversation_keeps_explicit_rules_but_skips_archive(monkeypatch):
    context = Mock(return_value="Explicit user rule: Keep answers concise.")
    monkeypatch.setattr(message_builder, "get_user_context", context)
    monkeypatch.setattr(
        message_builder, "get_self_context", lambda *_: "You are assisting Alex Carter."
    )
    messages = message_builder.build_main_messages(
        question="Tell me a joke",
        state=AgentState(goal="Tell me a joke", intent="conversational"),
        conversation_history=[{"role": "user", "content": "I like dry humor."}],
        user_email="alex@example.com",
        search_limit=30,
        client_context=None,
        skill_injector=Mock(side_effect=AssertionError("skill injection")),
    )
    context.assert_called_once_with("alex@example.com", "Tell me a joke", include_knowledge=False)
    assert any("Keep answers concise" in m["content"] for m in messages)
    assert any(m["content"] == "I like dry humor." for m in messages)
