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
            "json_schema": {"type": "object", "properties": {"status": {"type": "string"}}},
        },
    }
