from __future__ import annotations

import pytest

from evals import registry
from evals.types import EvalCase, EvalFlowDefinition, EvalLlmRequestOptions, EvalRunConfig


def test_list_eval_flows_includes_expected_flows():
    flows = registry.list_eval_flows()
    flow_ids = {flow["flow_id"] for flow in flows}

    assert "router" in flow_ids
    assert "contact_resolution" in flow_ids
    assert "event_extraction" in flow_ids
    assert "event_update_extraction" in flow_ids
    assert "contact_update_extraction" in flow_ids
    assert "tag_suggestion" in flow_ids


@pytest.mark.asyncio
async def test_run_eval_flow_aggregates_attempts(monkeypatch):
    async def execute_case(case, run_config: EvalRunConfig):
        return {
            "flow": case.case_id,
            "llm_model": run_config.llm_model,
            "user_email": run_config.user_email,
        }

    def score_case(case, output):
        return {"passed": output["llm_model"] == "test-model", "notes": []}

    def summarize_output(output):
        return {"llm_model": output["llm_model"]}

    fake_flow = EvalFlowDefinition(
        flow_id="fake",
        label="Fake",
        description="Fake flow for tests",
        cases=[EvalCase(case_id="case-1", title="Case 1", input={}, expected={})],
        execute_case=execute_case,
        score_case=score_case,
        summarize_output=summarize_output,
    )

    monkeypatch.setitem(registry._FLOW_MAP, "fake", fake_flow)
    warm_calls: list[tuple[str | None, int | None, str | int | None]] = []

    def fake_warm_chat_model(model, timeout=None, keep_alive=None):
        warm_calls.append((model, timeout, keep_alive))
        return True

    monkeypatch.setattr(registry, "warm_chat_model", fake_warm_chat_model)

    result = await registry.run_eval_flow(
        flow_id="fake",
        llm_model="test-model",
        repetitions=3,
        user_email="user@example.com",
    )

    assert result["flow"]["flow_id"] == "fake"
    assert result["summary"]["total_attempts"] == 3
    assert result["summary"]["measured_attempts"] == 2
    assert result["summary"]["discarded_attempts"] == 1
    assert result["summary"]["passed_attempts"] == 2
    assert result["cases"][0]["metrics"]["attempts"] == 2
    assert result["cases"][0]["metrics"]["total_attempts"] == 3
    assert result["cases"][0]["metrics"]["discarded_attempts"] == 1
    assert result["cases"][0]["metrics"]["variant_count"] == 1
    assert result["cases"][0]["attempts"][0]["discarded"] is True
    assert result["warmup"]["attempted"] is True
    assert result["warmup"]["performed"] is True
    assert result["warmup"]["keep_alive"] == registry.EVAL_LLM_KEEP_ALIVE
    assert result["discard_first_attempt"] is True
    assert result["keep_alive"] == registry.EVAL_LLM_KEEP_ALIVE
    assert result["request_options"]["stream"] is False
    assert result["request_options"]["strict_json_schema"] is True
    assert warm_calls == [("test-model", registry.EVAL_LLM_TIMEOUT, registry.EVAL_LLM_KEEP_ALIVE)]


@pytest.mark.asyncio
async def test_run_eval_flow_skips_warmup_without_model(monkeypatch):
    async def execute_case(case, run_config: EvalRunConfig):
        return {
            "flow": case.case_id,
            "llm_model": run_config.llm_model,
            "user_email": run_config.user_email,
        }

    fake_flow = EvalFlowDefinition(
        flow_id="fake-no-model",
        label="Fake No Model",
        description="Fake flow for tests",
        cases=[EvalCase(case_id="case-1", title="Case 1", input={}, expected={})],
        execute_case=execute_case,
        score_case=lambda case, output: {"passed": True, "notes": []},
        summarize_output=lambda output: {"llm_model": output["llm_model"]},
    )

    monkeypatch.setitem(registry._FLOW_MAP, "fake-no-model", fake_flow)

    result = await registry.run_eval_flow(
        flow_id="fake-no-model",
        llm_model=None,
        repetitions=1,
        user_email="user@example.com",
    )

    assert result["warmup"]["attempted"] is False
    assert result["warmup"]["performed"] is False
    assert result["discard_first_attempt"] is False


@pytest.mark.asyncio
async def test_run_eval_flow_can_keep_first_attempt_when_requested(monkeypatch):
    async def execute_case(case, run_config: EvalRunConfig):
        return {
            "flow": case.case_id,
            "llm_model": run_config.llm_model,
            "user_email": run_config.user_email,
        }

    fake_flow = EvalFlowDefinition(
        flow_id="fake-keep-first",
        label="Fake Keep First",
        description="Fake flow for tests",
        cases=[EvalCase(case_id="case-1", title="Case 1", input={}, expected={})],
        execute_case=execute_case,
        score_case=lambda case, output: {"passed": True, "notes": []},
        summarize_output=lambda output: {"llm_model": output["llm_model"]},
    )

    monkeypatch.setitem(registry._FLOW_MAP, "fake-keep-first", fake_flow)
    monkeypatch.setattr(registry, "warm_chat_model", lambda _model, timeout=None, keep_alive=None: True)

    result = await registry.run_eval_flow(
        flow_id="fake-keep-first",
        llm_model="test-model",
        repetitions=3,
        user_email="user@example.com",
        discard_first_attempt=False,
    )

    assert result["discard_first_attempt"] is False
    assert result["summary"]["measured_attempts"] == 3
    assert result["summary"]["discarded_attempts"] == 0
    assert all(attempt["discarded"] is False for attempt in result["cases"][0]["attempts"])


@pytest.mark.asyncio
async def test_run_eval_flow_includes_request_options_and_case_schemas(monkeypatch):
    async def execute_case(case, run_config: EvalRunConfig):
        return {
            "stream": run_config.request_options.stream,
            "reasoning_effort": run_config.request_options.reasoning_effort,
            "case_schema": case.response_json_schema,
        }

    fake_flow = EvalFlowDefinition(
        flow_id="fake-request-options",
        label="Fake Request Options",
        description="Fake flow for tests",
        cases=[
            EvalCase(
                case_id="case-1",
                title="Case 1",
                input={},
                expected={},
                response_json_schema={"type": "object"},
            )
        ],
        execute_case=execute_case,
        score_case=lambda case, output: {"passed": True, "notes": []},
        summarize_output=lambda output: output,
    )

    monkeypatch.setitem(registry._FLOW_MAP, "fake-request-options", fake_flow)
    monkeypatch.setattr(registry, "warm_chat_model", lambda _model, keep_alive=None: True)

    result = await registry.run_eval_flow(
        flow_id="fake-request-options",
        llm_model="test-model",
        repetitions=1,
        user_email="user@example.com",
        request_options=EvalLlmRequestOptions(
            stream=True,
            temperature=0,
            max_tokens=96,
            reasoning_effort="none",
            strict_json_schema=False,
        ),
    )

    assert result["request_options"]["stream"] is True
    assert result["request_options"]["max_tokens"] == 96
    assert result["request_options"]["reasoning_effort"] == "none"
    assert result["request_options"]["strict_json_schema"] is False
    assert result["cases"][0]["response_json_schema"] == {"type": "object"}
