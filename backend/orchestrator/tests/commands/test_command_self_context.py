from commands.handlers import contact as contact_handler
from commands.handlers import event as event_handler


def test_contact_extraction_uses_self_context_instead_of_raw_user_line(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "prompts.context.get_self_context",
        lambda _email: "You are assisting Alex. Known user aliases: Alex.",
    )
    monkeypatch.setattr(
        "prompts.context.get_user_facts_context",
        lambda *_args, **_kwargs: "Contact resolution rules:\n- Sage means Patricia.",
    )

    def fake_call_llm_json(prompt: str, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {
            "need_user_input": None,
            "main_contact_name": "Sage",
            "related_contact_name": None,
            "relationship_type": None,
            "birth_date_text": None,
            "place_text": None,
            "place_role": None,
            "contact_updates": [],
            "summary": "Update Sage",
        }

    monkeypatch.setattr("llm_helpers.call_llm_json", fake_call_llm_json)

    contact_handler._llm_extract_contact_changes(
        "Sage is a lawyer",
        user_email="alex@example.com",
        conversation_messages=[
            {"role": "user", "content": "Sage is a lawyer"},
            {"role": "assistant", "content": "Which Sage?"},
            {"role": "user", "content": "Patricia"},
        ],
        existing_extraction={"contacts": [{"contact_name": "Sage"}]},
    )

    prompt = str(captured["prompt"])
    assert "You are assisting Alex." in prompt
    assert "Contact resolution rules:" in prompt
    assert "Sage means Patricia." in prompt
    assert "Existing contact extraction from earlier turns" in prompt
    assert '"content": "Which Sage?"' in prompt
    assert '"contact_name": "Sage"' in prompt
    response_format = captured["kwargs"]["response_format"]  # type: ignore[index]
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    assert "contacts" in schema["properties"]
    assert "relationships" in schema["properties"]
    assert "reciprocal_type" in schema["properties"]["relationships"]["items"]["properties"]
    assert "contact_place_links" in schema["properties"]
    assert "Father/Daughter" in prompt
    assert "User: alex@example.com" not in prompt


def test_event_extraction_uses_self_context_instead_of_raw_user_line(monkeypatch):
    captured: dict[str, str] = {}

    monkeypatch.setattr("prompts.context.get_time_context", lambda: "Current test time")
    monkeypatch.setattr(
        "prompts.context.get_self_context",
        lambda _email: "You are assisting Alex. Known user aliases: Alex.",
    )
    monkeypatch.setattr("prompts.context.get_user_facts_context", lambda *_args, **_kwargs: None)

    def fake_call_llm_json(prompt: str, **_kwargs):
        captured["prompt"] = prompt
        return {
            "need_user_input": None,
            "title": "Lunch with Sage",
            "summary": "Had lunch with Sage.",
            "when": None,
            "end_when": None,
            "where": None,
            "documents": [],
            "tags": [],
            "types": ["personal"],
        }

    monkeypatch.setattr("llm_helpers.call_llm_json", fake_call_llm_json)

    event_handler._extract_event_entities_with_llm(
        "Lunch with Sage",
        {"user_email": "alex@example.com"},
    )

    prompt = captured["prompt"]
    assert "You are assisting Alex." in prompt
    assert "- User: alex@example.com" not in prompt


def test_event_follow_up_field_inference_uses_self_context(monkeypatch):
    captured: dict[str, str] = {}

    monkeypatch.setattr("prompts.context.get_time_context", lambda: "Current test time")
    monkeypatch.setattr(
        "prompts.context.get_self_context",
        lambda _email: "You are assisting Alex. Known user aliases: Alex.",
    )
    monkeypatch.setattr("prompts.context.get_user_facts_context", lambda *_args, **_kwargs: None)

    def fake_call_llm_json(prompt: str, **_kwargs):
        captured["prompt"] = prompt
        return {"fields": ["where"], "confidence": "high"}

    monkeypatch.setattr("llm_helpers.call_llm_json", fake_call_llm_json)

    fields = event_handler._infer_follow_up_target_fields(
        "at home",
        {"title": "Lunch", "where": None},
        {"user_email": "alex@example.com"},
    )

    assert fields == ["where"]
    prompt = captured["prompt"]
    assert "You are assisting Alex." in prompt
    assert "- User: alex@example.com" not in prompt


def test_event_extraction_infers_immediate_past_time(monkeypatch):
    monkeypatch.setattr("prompts.context.get_time_context", lambda: "Current test time")
    monkeypatch.setattr("prompts.context.get_self_context", lambda _email: None)
    monkeypatch.setattr("prompts.context.get_user_facts_context", lambda *_args, **_kwargs: None)

    fixed_now = event_handler.datetime(2026, 5, 7, 12, 30, 0)

    class FixedDateTime(event_handler.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return fixed_now.replace(tzinfo=tz)
            return fixed_now

    monkeypatch.setattr(event_handler, "datetime", FixedDateTime)
    monkeypatch.setattr(
        "llm_helpers.call_llm_json",
        lambda *_args, **_kwargs: {
            "need_user_input": {
                "prompt": "When did this happen?",
                "questions": ["When did this happen?"],
                "fields": [{"id": "when", "kind": "text", "label": "When", "required": True}],
            },
            "title": "Lunch",
            "summary": "Had lunch.",
            "when": None,
            "end_when": None,
            "where": None,
            "documents": [],
            "tags": [],
            "types": ["personal"],
        },
    )

    result = event_handler._extract_event_entities_with_llm(
        "I just had lunch",
        {"user_email": "alex@example.com"},
    )

    assert result["when"] == fixed_now
    assert result["need_user_input"] is None


def test_event_extraction_infers_relative_past_time(monkeypatch):
    monkeypatch.setattr("prompts.context.get_time_context", lambda: "Current test time")
    monkeypatch.setattr("prompts.context.get_self_context", lambda _email: None)
    monkeypatch.setattr("prompts.context.get_user_facts_context", lambda *_args, **_kwargs: None)

    fixed_now = event_handler.datetime(2026, 5, 7, 12, 30, 0)

    class FixedDateTime(event_handler.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return fixed_now.replace(tzinfo=tz)
            return fixed_now

    monkeypatch.setattr(event_handler, "datetime", FixedDateTime)
    monkeypatch.setattr(
        "llm_helpers.call_llm_json",
        lambda *_args, **_kwargs: {
            "need_user_input": None,
            "title": "Workout",
            "summary": "Finished a workout.",
            "when": None,
            "end_when": None,
            "where": None,
            "documents": [],
            "tags": [],
            "types": ["personal"],
        },
    )

    result = event_handler._extract_event_entities_with_llm(
        "Finished my workout 10 minutes ago",
        {"user_email": "alex@example.com"},
    )

    assert result["when"] == fixed_now - event_handler.timedelta(minutes=10)
