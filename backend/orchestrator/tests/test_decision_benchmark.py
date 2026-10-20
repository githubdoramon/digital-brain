from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from decision_benchmark.config import load_cases, load_models
from decision_benchmark.providers import headers_for, parse_prediction, request_for
from decision_benchmark.reporting import percentile
from decision_benchmark.runner import attempt, plan, run_benchmark
from decision_benchmark.tasks import TASKS, expected_answers, questions_for
from decision_benchmark.types import InvalidResponse, ModelConfig

ROOT = Path(__file__).resolve().parents[1]
CASES = load_cases()
CHAT = ModelConfig("chat", "openai", "test-model", "https://chat.example/v1/chat/completions")
JEV = ModelConfig("jev", "jev", "test-decision", "https://decision.example/v1/systemone")


def typed_response(case, *, confidence=0.9):
    answers = expected_answers(case)
    return {
        "answers": {
            key: {
                "type": "choice",
                "choice": answer,
                "confidence": confidence,
                "probabilities": {label: 1.0 if label == answer else 0.0 for label in q.criteria},
            }
            for key, q in questions_for(case).items()
            for answer in [answers[key]]
        }
    }


def chat_response(answers):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answers)}}]}


def run(tmp_path, cases, models, client, **kwargs):
    return run_benchmark(
        cases=cases,
        models=models,
        client=client,
        output=tmp_path / "result",
        suite_hash="suite-hash",
        config_hash="config-hash",
        repetitions=kwargs.get("repetitions", 2),
        warmups=kwargs.get("warmups", 1),
        seed=17,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_labelled_cases_have_identical_valid_answer_spaces_across_providers(case):
    expected = expected_answers(case)
    chat = parse_prediction(CHAT, case, chat_response(expected))
    jev = parse_prediction(JEV, case, typed_response(case))
    bridge = parse_prediction(replace(JEV, protocol="this_that"), case, typed_response(case))
    assert chat.answers == jev.answers == bridge.answers == expected
    chat_payload = request_for(CHAT, case)
    native_payload = request_for(JEV, case)
    prompt = json.loads(chat_payload["messages"][1]["content"])
    assert prompt["state"] == native_payload["state"] == case.input
    assert prompt["questions"] == native_payload["questions"]
    assert "expected" not in prompt
    assert "expected" not in native_payload
    assert set(chat_payload["response_format"]["json_schema"]["schema"]["required"]) == set(
        expected
    )


def test_suite_covers_all_tasks_and_both_languages():
    assert len(CASES) == 70
    assert {case.task for case in CASES} == set(TASKS)
    for task in TASKS:
        assert {case.language for case in CASES if case.task == task} == {"en", "pt"}


def test_full_suite_http_adapters_and_reports_agree_across_three_protocols(tmp_path):
    lookup = {json.dumps(case.input, sort_keys=True): case for case in CASES}
    bridge = replace(JEV, id="bridge", protocol="this_that", url="https://bridge.example/decide")

    def handle(request):
        payload = json.loads(request.content)
        if request.url.host == "chat.example":
            state = json.loads(payload["messages"][1]["content"])["state"]
            case = lookup[json.dumps(state, sort_keys=True)]
            return httpx.Response(200, json=chat_response(expected_answers(case)))
        case = lookup[json.dumps(payload["state"], sort_keys=True)]
        return httpx.Response(200, json=typed_response(case))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        report = run(tmp_path, CASES, [CHAT, JEV, bridge], client, repetitions=1)
    rows = [
        json.loads(line)
        for line in (tmp_path / "result" / "attempts.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 225  # 70 measured cases and 5 warm-ups, for three providers.
    for model_id in ("chat", "jev", "bridge"):
        group = report["summaries"][model_id]
        assert group["overall"]["accuracy"] == 1
        assert group["overall"]["unique_cases"] == 70
        assert group["overall"]["latency_valid"]["count"] == 70
        assert set(group["tasks"]) == set(TASKS)
        assert set(group["languages"]) == {"en", "pt"}
    assert report["summaries"]["chat"]["overall"]["confidence_slices"][0]["eligible_attempts"] == 0
    assert report["summaries"]["jev"]["overall"]["confidence_slices"][1]["coverage"] == 1


@pytest.mark.parametrize(
    "content",
    [
        {},
        {"intent": "invented"},
        {"intent": "neutral", "extra": "bad"},
        {"intent": True},
        {"intent": None},
    ],
)
def test_wrong_answer_spaces_are_rejected(content):
    with pytest.raises(InvalidResponse):
        parse_prediction(CHAT, CASES[0], chat_response(content))


@pytest.mark.parametrize("bad", ["missing", "probabilities", "nan", "sum", "argmax", "type"])
def test_typed_response_requires_complete_consistent_probabilities(bad):
    data = typed_response(CASES[0])
    answer = data["answers"]["intent"]
    if bad == "missing":
        del data["answers"]["intent"]
    elif bad == "probabilities":
        answer["probabilities"] = {"not_an_option": 1.0}
    elif bad == "nan":
        answer["confidence"] = float("nan")
    elif bad == "sum":
        answer["probabilities"]["neutral"] = 0.4
    elif bad == "argmax":
        answer["choice"] = "neutral"
    else:
        answer["type"] = "score"
    with pytest.raises(InvalidResponse):
        parse_prediction(JEV, CASES[0], data)


def test_structured_output_modes_and_budget_are_explicit():
    for mode in ("json_schema", "json_object", "none"):
        config = replace(
            CHAT,
            structured_output=mode,
            request_options={"max_tokens": 100, "reasoning_effort": "none"},
        )
        payload = request_for(config, CASES[0])
        assert payload["max_tokens"] == 100
        assert payload["reasoning_effort"] == "none"
        assert ("response_format" in payload) == (mode != "none")
        if mode != "none":
            assert payload["response_format"]["type"] == mode


def test_fenced_json_is_accepted_but_truncated_or_refused_answers_are_not():
    response = chat_response({"intent": "create_new"})
    response["choices"][0]["message"]["content"] = '```json\n{"intent":"create_new"}\n```'
    assert parse_prediction(CHAT, CASES[0], response).answers == {"intent": "create_new"}
    response["choices"][0]["finish_reason"] = "length"
    with pytest.raises(InvalidResponse):
        parse_prediction(CHAT, CASES[0], response)
    response["choices"][0]["finish_reason"] = "stop"
    response["choices"][0]["message"]["refusal"] = "Cannot answer"
    with pytest.raises(InvalidResponse):
        parse_prediction(CHAT, CASES[0], response)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"choices": []},
        {"choices": ["not-an-object"]},
        {"choices": [{"message": "not-an-object"}]},
        {"choices": [{"message": {"content": 'prefix {"intent":"create_new"}'}}]},
        {"choices": [{"message": {"content": '{"intent":"neutral","intent":"create_new"}'}}]},
    ],
)
def test_malformed_provider_payloads_become_failed_attempts_instead_of_aborting(payload):
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        row = attempt(client, CHAT, CASES[0], {})
    assert row["error"] == "invalid_response"
    assert not row["passed"]


def test_typed_unhashable_choice_is_a_failed_attempt():
    payload = typed_response(CASES[0])
    payload["answers"]["intent"]["choice"] = []
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        row = attempt(client, JEV, CASES[0], {})
    assert row["error"] == "invalid_response"


def test_auth_is_per_request_and_global_llm_settings_are_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_ONE", "first-secret")
    monkeypatch.setenv("BENCH_TWO", "second-secret")
    monkeypatch.setenv("LLM_BASE_URL", "https://production.example/v1")
    one = replace(CHAT, id="one", api_key_env="BENCH_ONE")
    two = replace(
        CHAT, id="two", api_key_env="BENCH_TWO", auth_header="x-litellm-api-key", auth_prefix=""
    )
    anonymous = replace(CHAT, id="anonymous")
    calls = []

    def handle(request):
        calls.append(request.headers)
        return httpx.Response(200, json=chat_response(expected_answers(CASES[0])))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        report = run(tmp_path, [CASES[0]], [one, two, anonymous], client, repetitions=1, warmups=0)
    assert calls[0]["authorization"] == "Bearer first-secret"
    assert calls[1]["x-litellm-api-key"] == "second-secret"
    assert "authorization" not in calls[1]
    assert "authorization" not in calls[2] and "x-litellm-api-key" not in calls[2]
    assert report["summaries"]["one"]["overall"]["accuracy"] == 1
    import os

    assert os.environ["LLM_BASE_URL"] == "https://production.example/v1"
    output = "".join(path.read_text() for path in (tmp_path / "result").iterdir())
    for secret in (
        "first-secret",
        "second-secret",
        "chat.example",
        "BENCH_ONE",
        CASES[0].input["message"],
    ):
        assert secret not in output


def test_errors_count_against_accuracy_warmups_do_not_and_reports_survive(tmp_path):
    count = 0

    def handle(request):
        nonlocal count
        count += 1
        if count == 1:  # A failed warm-up does not consume a measured repetition.
            return httpx.Response(503, text="private credential and provider error")
        if count == 2:
            return httpx.Response(401, text="private credential and provider error")
        return httpx.Response(200, json=chat_response(expected_answers(CASES[0])))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        report = run(tmp_path, [CASES[0]], [CHAT], client)
    stats = report["summaries"]["chat"]["overall"]
    assert count == 3
    assert stats["attempts"] == 2 and stats["correct"] == 1 and stats["errors"] == 1
    assert stats["accuracy"] == 0.5 and stats["valid_answer_accuracy"] == 1
    assert stats["latency_all"]["count"] == 2 and stats["latency_valid"]["count"] == 1
    assert stats["warmup_latency"]["count"] == 1
    assert stats["error_counts"] == {"http_401": 1}
    assert report["completed"]
    assert {p.name for p in (tmp_path / "result").iterdir()} == {
        "attempts.jsonl",
        "report.json",
        "summary.csv",
        "summary.txt",
    }
    assert "private credential" not in (tmp_path / "result" / "attempts.jsonl").read_text()


def test_exact_field_accuracy_requires_the_whole_set():
    case = next(case for case in CASES if case.id == "fields-two")
    wrong = expected_answers(case)
    wrong["when"] = "no"
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=chat_response(wrong))
        )
    ) as client:
        result = attempt(client, CHAT, case, {})
    assert result["error"] is None and not result["passed"]
    assert sum(result["question_passed"].values()) == 6


@pytest.mark.parametrize(
    "error,expected",
    [
        (httpx.ReadTimeout("secret"), "timeout"),
        (httpx.ConnectError("secret"), "connection_error"),
    ],
)
def test_network_failures_are_safe_codes(error, expected):
    def handle(request):
        raise error

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        row = attempt(client, CHAT, CASES[0], {})
    assert row["error"] == expected and not row["passed"]
    assert "secret" not in json.dumps(row)


def test_missing_credentials_fail_before_any_request_or_output(tmp_path, monkeypatch):
    monkeypatch.delenv("BENCH_MISSING", raising=False)
    model = replace(CHAT, api_key_env="BENCH_MISSING")
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: pytest.fail("network must not run"))
    ) as client:
        with pytest.raises(ValueError, match="BENCH_MISSING"):
            run(tmp_path, [CASES[0]], [model], client)
    assert not (tmp_path / "result").exists()


def test_interruption_preserves_completed_attempts_and_marks_report_partial(tmp_path):
    count = 0

    def handle(request):
        nonlocal count
        count += 1
        if count == 2:
            raise KeyboardInterrupt
        return httpx.Response(200, json=chat_response(expected_answers(CASES[0])))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        report = run(tmp_path, [CASES[0]], [CHAT], client, repetitions=3, warmups=0)
    assert report["interrupted"] and not report["completed"]
    assert report["planned_measured_attempts"] == 3
    assert report["summaries"]["chat"]["overall"]["attempts"] == 1
    assert len((tmp_path / "result" / "attempts.jsonl").read_text().splitlines()) == 1
    assert json.loads((tmp_path / "result" / "report.json").read_text())["interrupted"]


@pytest.mark.parametrize("accuracy,errors,expected_exit", [(0.5, 0, 1), (1.0, 0, 0), (1.0, 1, 1)])
def test_cli_accuracy_gate_and_request_errors_return_failure(
    tmp_path, monkeypatch, accuracy, errors, expected_exit
):
    from decision_benchmark.reporting import summarize_groups
    from scripts import benchmark_decisions

    row = {
        "model_id": "local-chat",
        "case_id": "test",
        "task": "event_matching",
        "language": "en",
        "phase": "measured",
        "first_request": True,
        "duration_ms": 1,
        "expected": {"intent": "neutral"},
        "answers": {"intent": "neutral"},
        "question_passed": {"intent": True},
        "confidence": {},
        "error": None,
        "passed": True,
    }
    summaries = summarize_groups([row], ["local-chat"])
    summaries["local-chat"]["overall"].update(accuracy=accuracy, errors=errors)
    monkeypatch.setattr(
        benchmark_decisions,
        "run_benchmark",
        lambda **kwargs: {"summaries": summaries, "interrupted": False},
    )
    status = benchmark_decisions.main(
        [
            "--config",
            str(ROOT / "decision_benchmark" / "models.example.json"),
            "--models",
            "local-chat",
            "--fail-under",
            "90",
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )
    assert status == expected_exit


def test_seeded_plan_rotates_models_and_preserves_every_repetition():
    models = [CHAT, JEV]
    cases = CASES[:3]
    jobs = list(plan(cases, models, 2, 1, 12))
    assert jobs == list(plan(cases, models, 2, 1, 12))
    measured = [job for job in jobs if job[0] == "measured"]
    assert len(measured) == 12
    assert len({(repeat, model.id, case.id) for _, repeat, model, case in measured}) == 12
    assert {measured[i][2].id for i in range(0, len(measured), 2)} == {"chat", "jev"}


@pytest.mark.parametrize(
    "changes",
    [
        {"timeout_seconds": 0},
        {"timeout_seconds": True},
        {"protocol": "made_up"},
        {"url": "https://user:secret@api.example/v1"},
        {"url": "https://api.example/v1?key=secret"},
        {"request_options": {"messages": []}},
        {"structured_output": "bad"},
        {"headers_env": {"X-Test": "not-an-env-name"}},
    ],
)
def test_invalid_model_config_fails_before_execution(tmp_path, changes):
    path = tmp_path / "models.json"
    row = {"id": "test", "protocol": "openai", "model": "test", "url": CHAT.url, **changes}
    path.write_text(json.dumps({"version": 1, "models": [row]}))
    with pytest.raises(ValueError):
        load_models(path)


def test_invalid_manual_labels_and_duplicate_ids_are_rejected(tmp_path):
    path = tmp_path / "cases.json"
    case = {
        "id": "test",
        "task": "event_matching",
        "language": "en",
        "input": {"message": "A new event"},
        "expected": {"intent": "misspelled"},
    }
    path.write_text(json.dumps({"version": 1, "cases": [case]}))
    with pytest.raises(ValueError, match="expected answer"):
        load_cases(path)
    case["expected"] = {"intent": "create_new"}
    path.write_text(json.dumps({"version": 1, "cases": [case, case]}))
    with pytest.raises(ValueError, match="Duplicate"):
        load_cases(path)


def test_dry_run_needs_no_credentials_or_output_directory(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "benchmark_decisions.py"),
            "--config",
            str(ROOT / "decision_benchmark" / "models.example.json"),
            "--models",
            "jev",
            "--dry-run",
            "--output-dir",
            str(tmp_path / "unused"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "210 measured + 5 warm-up" in result.stdout
    assert not (tmp_path / "unused").exists()


def test_percentiles_interpolate_and_empty_samples_remain_null():
    assert percentile([], 0.95) is None
    assert percentile([10, 30, 20], 0.5) == 20
    assert percentile([10, 20], 0.95) == 19.5


def test_headers_env_supports_optional_custom_auth(monkeypatch):
    monkeypatch.setenv("BENCH_CUSTOM", "custom-value")
    assert (
        headers_for(replace(CHAT, headers_env={"X-API-Key": "BENCH_CUSTOM"}))["X-API-Key"]
        == "custom-value"
    )
