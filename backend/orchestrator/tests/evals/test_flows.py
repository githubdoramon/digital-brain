from __future__ import annotations

from evals import flows
from evals.types import EvalCase, EvalLlmRequestOptions, EvalRunConfig


def test_contact_resolution_eval_forwards_request_options(monkeypatch):
    captured: dict[str, object] = {}

    def fake_resolve_contacts_request(data):
        captured.update(data)
        return {"status": "success", "people_mentioned": ["Dana Lewis"]}

    monkeypatch.setattr(flows, "resolve_contacts_request", fake_resolve_contacts_request)

    case = EvalCase(
        case_id="contact-case",
        title="Contact case",
        input={"text": "When did I last meet Dana?"},
        expected={},
        response_json_schema={"type": "object", "properties": {"status": {"type": "string"}}},
    )
    run_config = EvalRunConfig(
        llm_model="qwen3.5:0.8b",
        user_email="user@example.com",
        timeout_seconds=33,
        request_options=EvalLlmRequestOptions(
            stream=True,
            temperature=0,
            max_tokens=96,
            reasoning_effort="none",
        ),
    )

    result = flows._execute_contact_resolution_case(case, run_config)

    assert result["status"] == "success"
    assert captured["llm_model"] == "qwen3.5:0.8b"
    assert captured["timeout"] == 33
    assert captured["user_email"] == "user@example.com"
    assert captured["llm_request_options"] == {
        "stream": True,
        "temperature": 0,
        "max_tokens": 96,
        "reasoning_effort": "none",
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "eval_contact-case",
                "strict": True,
                "schema": {"type": "object", "properties": {"status": {"type": "string"}}},
            },
        },
    }


def test_contact_resolution_eval_can_disable_strict_json_schema(monkeypatch):
    captured: dict[str, object] = {}

    def fake_resolve_contacts_request(data):
        captured.update(data)
        return {"status": "success", "people_mentioned": ["Dana Lewis"]}

    monkeypatch.setattr(flows, "resolve_contacts_request", fake_resolve_contacts_request)

    case = EvalCase(
        case_id="contact-case-no-schema",
        title="Contact case no schema",
        input={"text": "When did I last meet Dana?"},
        expected={},
        response_json_schema={"type": "object", "properties": {"status": {"type": "string"}}},
    )
    run_config = EvalRunConfig(
        llm_model="qwen3.5:0.8b",
        user_email="user@example.com",
        timeout_seconds=33,
        request_options=EvalLlmRequestOptions(strict_json_schema=False),
    )

    flows._execute_contact_resolution_case(case, run_config)

    llm_request_options = captured["llm_request_options"]
    assert isinstance(llm_request_options, dict)
    assert llm_request_options["response_format"] is None


def test_score_contact_resolution_case_rejects_absent_mentions_present():
    case = EvalCase(
        case_id="contact-case-absent-mentions",
        title="Contact case absent mentions",
        input={"text": "I met Dana Lewis at the physiotherapy session."},
        expected={"people_mentioned": ["Dana Lewis"], "absent_mentions": ["I"]},
    )

    score = flows._score_contact_resolution_case(
        case,
        {"status": "success", "people_mentioned": ["Dana Lewis", "I"]},
    )

    assert score["passed"] is False
    assert "Unexpected mentions present: i" in score["notes"]


def test_score_event_extraction_accepts_utc_timezone_suffix_for_expected_naive_datetime():
    case = EvalCase(
        case_id="event-datetime",
        title="Event datetime",
        input={},
        expected={"when": "2026-06-02T09:00:00", "end_when": "2026-06-02T10:00:00"},
    )

    score = flows._score_event_extraction_case(
        case,
        {
            "when": "2026-06-02T09:00:00+00:00",
            "end_when": "2026-06-02T10:00:00Z",
        },
    )

    assert score["passed"] is True
    assert score["notes"] == []


def test_score_event_extraction_still_rejects_different_datetime():
    case = EvalCase(
        case_id="event-datetime-different",
        title="Event datetime different",
        input={},
        expected={"when": "2026-06-02T09:00:00"},
    )

    score = flows._score_event_extraction_case(
        case,
        {"when": "2026-06-02T10:00:00+00:00"},
    )

    assert score["passed"] is False
    assert "Expected when '2026-06-02T09:00:00'" in score["notes"][0]


def test_score_event_extraction_accepts_matching_date_only_values():
    case = EvalCase(
        case_id="event-date",
        title="Event date",
        input={},
        expected={"when": "2026-06-02"},
    )

    score = flows._score_event_extraction_case(case, {"when": "2026-06-02"})

    assert score["passed"] is True
