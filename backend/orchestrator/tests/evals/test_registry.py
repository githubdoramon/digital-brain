from __future__ import annotations

import pytest

from evals import registry
from evals.types import EvalCase, EvalFlowDefinition


def test_list_eval_flows_includes_expected_flows():
    flows = registry.list_eval_flows()
    flow_ids = {flow["flow_id"] for flow in flows}

    assert "router" in flow_ids
    assert "contact_resolution" in flow_ids
    assert "event_extraction" in flow_ids
    assert "contact_update_extraction" in flow_ids
    assert "tag_suggestion" in flow_ids


@pytest.mark.asyncio
async def test_run_eval_flow_aggregates_attempts(monkeypatch):
    async def execute_case(case, llm_model, user_email):
        return {"flow": case.case_id, "llm_model": llm_model, "user_email": user_email}

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

    result = await registry.run_eval_flow(
        flow_id="fake",
        llm_model="test-model",
        repetitions=3,
        user_email="user@example.com",
    )

    assert result["flow"]["flow_id"] == "fake"
    assert result["summary"]["total_attempts"] == 3
    assert result["summary"]["passed_attempts"] == 3
    assert result["cases"][0]["metrics"]["variant_count"] == 1
