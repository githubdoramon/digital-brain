from agent.state import AgentState
from agents.memory_expert.message_builder import _cached_tag_context, build_memory_expert_messages
from prompts.system import KNOWLEDGE_LEARNING_SYSTEM_PROMPT


def test_memory_expert_includes_tag_context(monkeypatch):
    _cached_tag_context.cache_clear()
    monkeypatch.setattr(
        "agents.memory_expert.message_builder.get_tag_context",
        lambda: "Tag taxonomy for filtering searches:\n- Work: project, meeting",
    )
    monkeypatch.setattr(
        "agents.memory_expert.message_builder.inject_memory_expert_skills",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "agents.memory_expert.message_builder.get_user_context",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "agents.memory_expert.message_builder.get_self_context",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "agents.memory_expert.message_builder.get_location_context",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "agents.memory_expert.message_builder.get_time_context",
        lambda: "Current time context available to you:",
    )

    messages = build_memory_expert_messages(
        question="Summarize my work last week",
        state=AgentState(goal="Summarize my work last week"),
        conversation_history=None,
        user_email="user@example.com",
        search_limit=30,
        client_context=None,
    )

    contents = [message["content"] for message in messages if message["role"] == "system"]
    assert any("Tag taxonomy for filtering searches" in content for content in contents)
    assert contents.count(KNOWLEDGE_LEARNING_SYSTEM_PROMPT) == 1


def test_main_profile_imports_the_shared_learning_guidance(monkeypatch):
    from agents.main import message_builder

    monkeypatch.setattr(message_builder, "_cached_tag_context", lambda: "")
    messages = message_builder.build_main_messages(
        question="Remember these family details",
        state=AgentState(goal="Remember these family details"),
        conversation_history=None,
        user_email=None,
        search_limit=30,
        client_context=None,
        skill_injector=lambda *_: None,
    )
    contents = [m["content"] for m in messages if m["role"] == "system"]
    assert contents.count(KNOWLEDGE_LEARNING_SYSTEM_PROMPT) == 1
