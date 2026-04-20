from commands.handlers import contact as contact_handler
from commands.handlers import event as event_handler


def test_contact_extraction_uses_self_context_instead_of_raw_user_line(monkeypatch):
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        "prompts.context.get_self_context",
        lambda _email: "You are assisting Ramon. Known user aliases: Ramon.",
    )
    monkeypatch.setattr(
        "prompts.context.get_user_facts_context",
        lambda *_args, **_kwargs: "Contact resolution rules:\n- Sage means Patricia.",
    )

    def fake_call_llm_json(prompt: str, **_kwargs):
        captured["prompt"] = prompt
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

    contact_handler._llm_extract_contact_changes("Sage is a lawyer", user_email="user@example.com")

    prompt = captured["prompt"]
    assert "You are assisting Ramon." in prompt
    assert "Contact resolution rules:" in prompt
    assert "Sage means Patricia." in prompt
    assert '"contacts": [' in prompt
    assert '"relationships": [' in prompt
    assert '"contact_place_links": [' in prompt
    assert "User: user@example.com" not in prompt


def test_event_extraction_uses_self_context_instead_of_raw_user_line(monkeypatch):
    captured: dict[str, str] = {}

    monkeypatch.setattr("prompts.context.get_time_context", lambda: "Current test time")
    monkeypatch.setattr(
        "prompts.context.get_self_context",
        lambda _email: "You are assisting Ramon. Known user aliases: Ramon.",
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
        {"user_email": "user@example.com"},
    )

    prompt = captured["prompt"]
    assert "You are assisting Ramon." in prompt
    assert "- User: user@example.com" not in prompt


def test_event_follow_up_field_inference_uses_self_context(monkeypatch):
    captured: dict[str, str] = {}

    monkeypatch.setattr("prompts.context.get_time_context", lambda: "Current test time")
    monkeypatch.setattr(
        "prompts.context.get_self_context",
        lambda _email: "You are assisting Ramon. Known user aliases: Ramon.",
    )
    monkeypatch.setattr("prompts.context.get_user_facts_context", lambda *_args, **_kwargs: None)

    def fake_call_llm_json(prompt: str, **_kwargs):
        captured["prompt"] = prompt
        return {"fields": ["where"], "confidence": "high"}

    monkeypatch.setattr("llm_helpers.call_llm_json", fake_call_llm_json)

    fields = event_handler._infer_follow_up_target_fields(
        "at home",
        {"title": "Lunch", "where": None},
        {"user_email": "user@example.com"},
    )

    assert fields == ["where"]
    prompt = captured["prompt"]
    assert "You are assisting Ramon." in prompt
    assert "- User: user@example.com" not in prompt
