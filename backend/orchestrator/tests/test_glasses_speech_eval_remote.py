import json
from unittest.mock import MagicMock

import pytest
import requests

import glasses_speech_eval_remote as remote
from glasses_speech_eval import EvalFailure
from tests.test_glasses_speech_eval import client, wav


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "true")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("GLASSES_SPEECH_EVAL_REMOTE_BASE_URL", raising=False)
    monkeypatch.delenv("GLASSES_SPEECH_EVAL_REMOTE_API_KEY", raising=False)


def stub_post(monkeypatch, *, status=200, body=None, chunks=None):
    response = MagicMock(status_code=status)
    response.iter_content.return_value = (
        chunks
        if chunks is not None
        else [
            json.dumps(body if body is not None else {"text": "Hey Brain take a picture"}).encode()
        ]
    )
    response.__enter__.return_value = response
    post = MagicMock(return_value=response)
    monkeypatch.setattr(remote.requests, "post", post)
    return post


@pytest.mark.parametrize(
    "base,expected",
    [
        ("https://provider.invalid", "https://provider.invalid/v1/audio/transcriptions"),
        ("https://provider.invalid/v1/", "https://provider.invalid/v1/audio/transcriptions"),
        (
            "https://provider.invalid/gateway",
            "https://provider.invalid/gateway/audio/transcriptions",
        ),
    ],
)
def test_configured_base_paths(monkeypatch, base, expected):
    monkeypatch.setenv("LLM_BASE_URL", base)
    assert remote.configuration() == (expected, "test-key")


def test_overrides_and_explicit_no_key(monkeypatch):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_REMOTE_BASE_URL", "https://speech.invalid/v1")
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_REMOTE_API_KEY", "speech-key")
    assert remote.configuration() == (
        "https://speech.invalid/v1/audio/transcriptions",
        "speech-key",
    )
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_REMOTE_API_KEY", "")
    assert remote.configuration()[1] == ""


def test_transcribe_uploads_only_audio_and_fixed_parameters(monkeypatch, caplog):
    post = stub_post(monkeypatch)
    data = wav()
    with caplog.at_level("INFO", logger=remote.__name__):
        result = remote.request(remote.MODEL, "transcribe", data)
    args, kwargs = post.call_args
    assert args == ("https://provider.invalid/v1/audio/transcriptions",)
    assert kwargs["headers"] == {"Authorization": "Bearer test-key"}
    assert kwargs["files"] == {"file": ("sample.wav", data, "audio/wav")}
    assert kwargs["data"] == {
        "model": "whisper-large-v3-turbo",
        "language": "en",
        "response_format": "json",
        "temperature": "0",
    }
    assert kwargs["allow_redirects"] is False
    assert result["text"] == "Hey Brain take a picture"
    assert result["coldStartControlled"] is False
    assert result["provider"] == "remote"
    assert result["model"] == "large-v3-turbo"
    assert result["upstreamModel"] == "whisper-large-v3-turbo"
    assert result["upstreamRequestMs"] >= 0
    assert "modelAlreadyLoaded" not in result
    assert "inferenceMs" not in result
    for private in ("Hey Brain", "test-key", "provider.invalid"):
        assert private not in caplog.text


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_http_failure_is_safe_and_slot_released(monkeypatch, caplog, status):
    stub_post(monkeypatch, status=status, body={"detail": "private credentials"})
    with pytest.raises(EvalFailure) as exc:
        remote.request(remote.MODEL, "transcribe", wav())
    assert str(exc.value) == "remote_http_error"
    assert exc.value.http_status == status
    assert "private credentials" not in caplog.text
    assert remote._slot.acquire(blocking=False)
    remote._slot.release()


@pytest.mark.parametrize("chunks", [[b"not json"], [b"[]"], [b'{"text":null}'], [b"x" * 32769]])
def test_invalid_and_oversized_responses(monkeypatch, chunks):
    stub_post(monkeypatch, chunks=chunks)
    with pytest.raises(EvalFailure, match="remote_invalid_response"):
        remote.request(remote.MODEL, "transcribe", wav())


@pytest.mark.parametrize(
    "error,reason",
    [
        (requests.Timeout("private endpoint"), "network_timeout"),
        (requests.ConnectionError("private credentials"), "network_unavailable"),
    ],
)
def test_network_failure_is_safe(monkeypatch, caplog, error, reason):
    monkeypatch.setattr(remote.requests, "post", MagicMock(side_effect=error))
    with pytest.raises(EvalFailure, match=reason):
        remote.request(remote.MODEL, "transcribe", wav())
    assert "private" not in caplog.text


def test_prepare_never_claims_to_load_remote_model(monkeypatch):
    post = stub_post(monkeypatch)
    result = remote.request(remote.MODEL, "prepare")
    assert result["prepared"] is True
    assert result["coldStartControlled"] is False
    post.assert_not_called()
    for model, operation, audio, threads, reason in [
        ("unknown", "prepare", b"", None, "invalid_model"),
        (remote.MODEL, "prepare", b"", 4, "invalid_cpu_threads"),
        (remote.MODEL, "transcribe", b"invalid wav", None, "invalid_audio"),
    ]:
        with pytest.raises(EvalFailure, match=reason):
            remote.request(model, operation, audio, threads)
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "false")
    with pytest.raises(EvalFailure, match="evaluation_disabled"):
        remote.request(remote.MODEL, "prepare")
    post.assert_not_called()


def test_busy_remote_slot_does_not_queue_parallel_inference(monkeypatch):
    post = stub_post(monkeypatch)
    assert remote._slot.acquire(blocking=False)
    try:
        with pytest.raises(EvalFailure, match="server_busy"):
            remote.request(remote.MODEL, "transcribe", wav())
        post.assert_not_called()
    finally:
        remote._slot.release()


def test_route_dispatch_is_explicit_and_authenticated(monkeypatch):
    post = stub_post(monkeypatch)
    url = "/mobile/glasses/speech-eval/transcribe?provider=remote&model=large-v3-turbo&cold=true"
    assert client(False).post(url, content=wav()).status_code == 401
    post.assert_not_called()
    response = client().post(url, content=wav())
    assert response.status_code == 200
    assert response.json()["provider"] == "remote"
    assert client().post(url + "&threads=4", content=wav()).status_code == 400
    assert client().post(url.replace("remote", "unknown"), content=wav()).status_code == 422


@pytest.mark.parametrize(
    "base",
    [
        "",
        "file:///tmp/model",
        "https://user:secret@provider.invalid/v1",
        "http://[",
        "https://provider.invalid/v1?key=secret",
    ],
)
def test_bad_configuration_is_safe(monkeypatch, base):
    monkeypatch.setenv("LLM_BASE_URL", base)
    with pytest.raises(EvalFailure, match="remote_not_configured"):
        remote.configuration()
