from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient

from decision_benchmark.this_that_bridge import create_app


def runtime():
    decider = Mock()
    decider.tokenizer.encode.side_effect = lambda text, **kwargs: list(text)
    decider.decide.return_value = [
        SimpleNamespace(index=1, confidence=0.8, probabilities=(0.2, 0.8))
    ]
    return decider


def body():
    return {
        "model": "test-checkpoint",
        "state": {"message": "sample"},
        "questions": {
            "intent": {
                "type": "choice",
                "instructions": "Choose",
                "criteria": {"one": "First option", "two": "Second option"},
            }
        },
    }


def test_bridge_maps_native_indices_and_sends_full_rubrics_without_truncation():
    decider = runtime()
    factory = Mock(side_effect=lambda text, options: SimpleNamespace(text=text, options=options))
    with TestClient(
        create_app("test-checkpoint", decider=decider, question_factory=factory)
    ) as client:
        response = client.post("/decide", json=body())
    assert response.status_code == 200
    assert response.json()["answers"]["intent"] == {
        "type": "choice",
        "choice": "two",
        "confidence": 0.8,
        "probabilities": {"one": 0.2, "two": 0.8},
    }
    assert factory.call_args.args[1] == ["one: First option", "two: Second option"]
    assert '"message": "sample"' in decider.decide.call_args.args[0]
    assert decider.decide.call_args.kwargs["max_state_tokens"] > len(
        decider.decide.call_args.args[0]
    )
    assert decider.backend.synchronize.call_count == 2


def test_bridge_rejects_wrong_model_and_overlong_state_without_inference():
    decider = runtime()
    with TestClient(
        create_app("test-checkpoint", max_state_tokens=5, decider=decider, question_factory=Mock())
    ) as client:
        assert client.post("/decide", json=body()).status_code == 413
        payload = body()
        payload["model"] = "wrong-checkpoint"
        assert client.post("/decide", json=payload).status_code == 400
    decider.decide.assert_not_called()


def test_bridge_auth_and_errors_never_echo_state_or_inference_exceptions(monkeypatch):
    monkeypatch.setenv("BENCH_BRIDGE_KEY", "private-key")
    decider = runtime()
    decider.decide.side_effect = RuntimeError("private-inference-state")
    app = create_app(
        "test-checkpoint", api_key_env="BENCH_BRIDGE_KEY", decider=decider, question_factory=Mock()
    )
    with TestClient(app) as client:
        assert client.post("/decide", json=body()).status_code == 401
        response = client.post(
            "/decide", json=body(), headers={"Authorization": "Bearer private-key"}
        )
        assert response.status_code == 500
        assert response.json() == {"detail": "inference_failed"}
        payload = body()
        payload["questions"]["intent"]["criteria"] = {"secret-state": "only one option"}
        response = client.post("/decide", json=payload)
        assert response.status_code == 422
        assert "secret-state" not in response.text
