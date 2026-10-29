import json
from unittest.mock import MagicMock

import pytest
import requests

import speech_transcription as remote
from speech_transcription import EvalFailure
from tests.test_glasses_speech_eval import client, wav


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.delenv("SPEECH_BASE_URL", raising=False)
    monkeypatch.delenv("SPEECH_API_KEY", raising=False)
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "true")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("GLASSES_TRANSCRIPTION_BASE_URL", raising=False)
    monkeypatch.delenv("GLASSES_TRANSCRIPTION_API_KEY", raising=False)
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
    assert remote._slot.acquire(blocking=False)
    try:
        with pytest.raises(EvalFailure, match="server_busy"):
            remote.request(remote.MODEL, "transcribe", wav())
        post.assert_not_called()
    finally:
        remote._slot.release()
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


def test_live_transcription_works_when_replay_is_off(monkeypatch):
    post = stub_post(monkeypatch)
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "false")
    assert remote.transcribe(wav())["provider"] == "remote"
    post.assert_called_once()


def test_new_speech_settings_override_both_legacy_and_llm(monkeypatch):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_REMOTE_BASE_URL", "https://legacy.example/v1")
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_REMOTE_API_KEY", "legacy-key")
    monkeypatch.setenv("GLASSES_TRANSCRIPTION_BASE_URL", "https://speech.example/v1")
    monkeypatch.setenv("GLASSES_TRANSCRIPTION_API_KEY", "speech-key")
    assert remote.configuration() == (
        "https://speech.example/v1/audio/transcriptions",
        "speech-key",
    )


def test_live_invalid_audio_never_reaches_provider(monkeypatch):
    post = stub_post(monkeypatch)
    response = client().post("/mobile/glasses/transcribe", content=b"invalid wav")
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_audio"
    post.assert_not_called()


def test_live_provider_failure_retains_only_safe_status(monkeypatch):
    stub_post(monkeypatch, status=400, body={"error": "private provider text"})
    response = client().post("/mobile/glasses/transcribe", content=wav())
    assert response.status_code == 503
    assert response.json() == {
        "detail": "remote_http_error",
        "failureStage": "inference",
        "upstreamHttpStatus": 400,
    }


def test_dictation_uses_same_provider_and_allows_longer_recordings(monkeypatch):
    post = stub_post(monkeypatch)
    data = wav(seconds=31)
    url = "/mobile/speech/transcribe"
    assert client(False).post(url, content=data).status_code == 401
    post.assert_not_called()
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "false")
    response = client().post(url, content=data)
    assert response.status_code == 200
    assert post.call_args.kwargs["files"]["file"][1] == data
    assert post.call_args.kwargs["data"]["model"] == "whisper-large-v3-turbo"
    assert post.call_args.kwargs["timeout"] == (5, 60)
    assert client().post("/mobile/glasses/transcribe", content=data).status_code == 413
    assert post.call_count == 1


def test_dictation_upload_bounds_and_format_before_provider(monkeypatch):
    post = stub_post(monkeypatch)
    monkeypatch.setattr(remote, "MAX_DICTATION_BYTES", 128)
    assert client().post("/mobile/speech/transcribe", content=b"x" * 129).status_code == 413
    assert client().post("/mobile/speech/transcribe", content=b"bad wav").status_code == 400
    post.assert_not_called()
    with pytest.raises(EvalFailure, match="audio_too_large"):
        remote.validate_audio(wav(seconds=601), 600)


def test_shared_speech_settings_take_precedence(monkeypatch):
    monkeypatch.setenv("SPEECH_BASE_URL", "https://dictation.example/v1")
    monkeypatch.setenv("SPEECH_API_KEY", "shared-key")
    monkeypatch.setenv("GLASSES_TRANSCRIPTION_API_KEY", "legacy-key")
    assert remote.configuration() == (
        "https://dictation.example/v1/audio/transcriptions",
        "shared-key",
    )


def test_parakeet_replay_uses_its_model_without_whisper_parameters(monkeypatch):
    post = stub_post(monkeypatch)
    result = remote.request("parakeet-tdt-0.6b-v3", "transcribe", wav())
    assert post.call_args.kwargs["data"] == {
        "model": "parakeet-tdt-0.6b-v3",
        "response_format": "json",
    }
    assert result["model"] == result["upstreamModel"] == "parakeet-tdt-0.6b-v3"
    assert result["decoding"] == {"language": "provider_default"}
    assert result["coldStartControlled"] is False
    assert (
        client()
        .post("/mobile/glasses/speech-eval/prepare?provider=remote&model=parakeet-tdt-0.6b-v3")
        .json()["model"]
        == "parakeet-tdt-0.6b-v3"
    )


def test_parakeet_does_not_change_production_transcription(monkeypatch):
    post = stub_post(monkeypatch)
    remote.transcribe(wav())
    assert post.call_args.kwargs["data"]["model"] == "whisper-large-v3-turbo"
    remote.transcribe(wav(), dictation=True)
    assert post.call_args.kwargs["data"]["model"] == "whisper-large-v3-turbo"
    with pytest.raises(EvalFailure, match="invalid_model"):
        remote.request("parakeet-tdt-0.6b-v3", "transcribe", wav(), replay=False)
    assert post.call_count == 2


def test_whisper_vocabulary_is_explicit_versioned_and_replay_only(monkeypatch):
    post = stub_post(monkeypatch)
    url = "/mobile/glasses/speech-eval/transcribe?model=large-v3-turbo&vocabulary=commands-v1"
    result = client().post(url, content=wav())
    assert result.status_code == 200
    assert post.call_args.kwargs["data"]["prompt"] == remote.COMMAND_VOCABULARY
    assert result.json()["vocabularyProfile"] == "commands-v1"
    assert result.json()["vocabularyMethod"] == "prompt"
    for dictation in (False, True):
        remote.transcribe(wav(), dictation=dictation)
        assert "prompt" not in post.call_args.kwargs["data"]
    with pytest.raises(EvalFailure, match="invalid_vocabulary"):
        remote.request(remote.MODEL, "transcribe", wav(), replay=False, vocabulary="commands-v1")
    with pytest.raises(EvalFailure, match="invalid_vocabulary"):
        remote.request(remote.MODEL, "prepare", vocabulary="unknown")
    assert client().post(url.replace("commands-v1", "unknown"), content=wav()).status_code == 422


def test_parakeet_boosting_requires_distinct_configured_server_alias(monkeypatch):
    monkeypatch.delenv("SPEECH_EVAL_PARAKEET_BOOSTED_MODEL", raising=False)
    post = stub_post(monkeypatch)
    url = "/mobile/glasses/speech-eval/prepare?model=parakeet-tdt-0.6b-v3&vocabulary=commands-v1"
    result = client().post(url)
    assert result.status_code == 503
    assert result.json()["detail"] == "vocabulary_not_configured"
    post.assert_not_called()
    monkeypatch.setenv("SPEECH_EVAL_PARAKEET_BOOSTED_MODEL", "parakeet-tdt-0.6b-v3")
    assert client().post(url).json()["detail"] == "vocabulary_not_configured"
    monkeypatch.setenv("SPEECH_EVAL_PARAKEET_BOOSTED_MODEL", "parakeet-commands-v1")
    assert client().post(url).json()["upstreamModel"] == "parakeet-commands-v1"
    result = remote.request("parakeet-tdt-0.6b-v3", "transcribe", wav(), vocabulary="commands-v1")
    assert post.call_args.kwargs["data"] == {"model": "parakeet-commands-v1", "response_format": "json"}
    assert result["vocabularyProfile"] == "commands-v1"
    assert result["vocabularyMethod"] == "server_alias"
    remote.request("parakeet-tdt-0.6b-v3", "transcribe", wav())
    assert post.call_args.kwargs["data"]["model"] == "parakeet-tdt-0.6b-v3"
