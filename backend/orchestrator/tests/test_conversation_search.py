from __future__ import annotations

import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import conversation_search as search
from agent.state import AgentState
from tools.handlers.memory import handle_get_conversation, handle_search_memories


def test_interactive_embedding_timeout_cooldown_and_cache(monkeypatch):
    monkeypatch.setattr(search, "_QUERY_CACHE", search.OrderedDict())
    monkeypatch.setattr(search, "_QUERY_FAILURE_UNTIL", 0.0)
    calls = []

    def unavailable(text, **kwargs):
        calls.append(kwargs)
        raise search.embeddings.requests.ConnectTimeout()

    monkeypatch.setattr(search.embeddings, "embed_text", unavailable)
    assert search.query_embedding("garden watering") is None
    assert search.query_embedding("another search") is None
    assert len(calls) == 1
    assert calls[0]["request_timeout"] == (0.5, 2.0)
    assert calls[0]["log_response_body"] is False

    monkeypatch.setattr(search, "_QUERY_FAILURE_UNTIL", 0.0)
    monkeypatch.setattr(
        search.embeddings,
        "embed_text",
        lambda text, **kwargs: calls.append(kwargs) or [1.0] + [0.0] * 767,
    )
    assert search.query_embedding("garden watering") == search.query_embedding("garden watering")
    assert len(calls) == 2


def test_passages_keep_late_content_and_respect_multibyte_budget():
    text = "Préférence 🪴 " * 500 + "The final correction is weekly."
    passages = search.split_passages(text)
    assert len(passages) > 2
    assert all(len(part.encode()) <= 600 for part in passages)
    assert " ".join(" ".join(passages).split()) == " ".join(text.split())
    assert passages[-1].endswith("weekly.")


def test_merge_deduplicates_assistant_recall_and_preserves_user_attribution():
    memories = [{"kind": "document", "id": "doc-1", "snippet": "Original decision", "score": 12}]
    refs = [{"entity_type": "document", "entity_id": "doc-1"}]
    conversations = [
        {
            "kind": "conversation",
            "id": "1",
            "role": "assistant",
            "snippet": "Repeated answer",
            "source_items": refs,
        },
        {
            "kind": "conversation",
            "id": "2",
            "role": "user",
            "snippet": "I corrected the schedule",
            "source_items": refs,
        },
    ]
    rows = search.merge_memory_results(memories, conversations, limit=10, sort_order="relevance")
    assert [row["id"] for row in rows] == ["doc-1", "2"]
    assert rows[1]["role"] == "user"


def test_merge_sorts_by_message_date_and_keeps_unbounded_results():
    memories = [
        {"kind": "event", "id": "event-1", "start_date": "2025-01-01T00:00:00Z", "score": 1}
    ]
    chats = [{"kind": "conversation", "id": "2", "created_at": "2025-02-01T00:00:00Z"}]
    assert [
        r["id"]
        for r in search.merge_memory_results(memories, chats, limit=None, sort_order="newest")
    ] == ["2", "event-1"]
    assert [
        r["id"] for r in search.merge_memory_results(memories, chats, limit=1, sort_order="oldest")
    ] == ["event-1"]


def test_memory_search_injects_owner_current_thread_and_time_scope(monkeypatch):
    import messaging_archive
    monkeypatch.setattr(messaging_archive, "search", lambda *a, **k: [])
    monkeypatch.setitem(
        sys.modules, "retrieval", SimpleNamespace(search_memories=lambda *a, **k: {"results": []})
    )
    calls = []
    monkeypatch.setattr(search, "search_messages", lambda *a, **k: calls.append((a, k)) or [])
    handle_search_memories(
        {"query": "our schedule", "time_start": "2025-01-01"},
        state=AgentState(goal="recall", current_thread_id="current"),
        user_email="user@example.com",
    )
    assert calls[0][0] == ("user@example.com", "our schedule")
    assert calls[0][1]["exclude_thread_id"] == "current"
    assert calls[0][1]["time_start"] == "2025-01-01"


def test_structured_filters_do_not_become_unverified_chat_associations(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "retrieval", SimpleNamespace(search_memories=lambda *a, **k: {"results": []})
    )
    monkeypatch.setattr(
        search,
        "search_messages",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not search chats")),
    )
    result = handle_search_memories(
        {"query": "schedule", "tags": ["work"]}, user_email="user@example.com"
    )
    assert "no verified" in result["conversation_scope_note"]


def test_get_context_marks_only_inspected_message_and_uses_matching_passage(monkeypatch):
    state = AgentState(goal="recall", current_thread_id="current")
    state.remember_information_candidate("conversation", "42", metadata={"snippet": "late passage"})
    calls = []

    def context(*args, **kwargs):
        calls.append((args, kwargs))
        return {
            "title": "Garden plans",
            "messages": [
                {
                    "message_id": 42,
                    "content": "I changed my mind",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        }

    monkeypatch.setattr(search, "get_context", context)
    handle_get_conversation(
        {"thread_id": "previous", "message_id": 42}, state=state, user_email="user@example.com"
    )
    assert calls[0][1]["focus"] == "late passage"
    assert state.information_candidates[0]["inspected"]
    assert state.information_candidates[0]["metadata"]["thread_id"] == "previous"
    assert handle_get_conversation(
        {"thread_id": "current", "message_id": 42}, state=state, user_email="user@example.com"
    ) == {"conversation": None}


def test_controller_links_only_inspected_conversation_and_preserves_message_id():
    from agent.controller import AgentController

    state = AgentState(goal="What was our garden watering decision?")
    state.remember_information_candidate(
        "conversation",
        "42",
        label="Garden watering decision",
        score=0.9,
        query=state.goal,
        metadata={"thread_id": "garden", "message_id": 42, "snippet": "Water weekly"},
    )
    controller = AgentController()
    assert controller._build_linked_items(state, answer="We chose weekly garden watering.") == []
    state.mark_information_candidate_inspected("conversation", "42")
    links = controller._build_linked_items(state, answer="We chose weekly garden watering.")
    assert links[0]["entity_type"] == "conversation"
    assert links[0]["entity_id"] == "garden"
    assert links[0]["message_id"] == 42


def test_source_changes_are_distinct_searches_and_do_not_force_contact_associations(monkeypatch):
    from agent.controller import AgentController

    controller = AgentController()
    assert controller._memory_search_signature(
        {"query": "garden"}
    ) != controller._memory_search_signature({"query": "garden", "sources": ["conversation"]})
    monkeypatch.setattr(
        controller,
        "_ensure_contact_scope",
        lambda **k: (_ for _ in ()).throw(
            AssertionError("conversation mentions need no contact association")
        ),
    )
    args, preempt = controller._prepare_memory_search_arguments(
        {"query": "Alex's garden", "sources": ["conversation"]},
        AgentState(goal="Recall our chat"),
        "Recall our chat",
        "user@example.com",
        None,
    )
    assert preempt is None and not args.get("contact_ids")


def test_conversation_context_compaction_retains_matching_passage_and_provenance():
    import json

    from agent.tool_executor import MAX_COMPACT_TOOL_PAYLOAD_CHARS, ToolExecutionCoordinator

    coordinator = ToolExecutionCoordinator(SimpleNamespace())
    result = {
        "conversation": {
            "thread_id": "garden",
            "message_id": 42,
            "title": "Garden decisions",
            "messages": [
                {
                    "message_id": mid,
                    "role": "assistant" if mid == 42 else "user",
                    "created_at": "2025-01-01T10:00:00Z",
                    "content": "x" * 1500 + "matching passage" + "x" * 4490,
                    "source_items": [
                        {"entity_type": "document", "entity_id": "doc-1", "title": "Original plan"}
                    ],
                }
                for mid in range(40, 45)
            ],
        }
    }
    payload = coordinator.build_tool_message_payload(
        "get_conversation",
        result,
        messages=[{"role": "user", "content": "Earlier context " * 10000}],
    )
    assert len(json.dumps(payload)) <= MAX_COMPACT_TOOL_PAYLOAD_CHARS
    target = next(row for row in payload["conversation"]["messages"] if row["message_id"] == 42)
    assert "matching passage" in target["content"]
    assert target["role"] == "assistant" and target["created_at"]
    assert target["source_items"][0]["entity_id"] == "doc-1"
    assert [row["message_id"] for row in payload["conversation"]["messages"]] == list(range(40, 45))
    assert payload["_meta"]["compacted_for_budget"]
