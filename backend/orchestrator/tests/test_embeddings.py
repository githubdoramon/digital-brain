from __future__ import annotations

from typing import Any

import embeddings


def test_get_embeddings_headers_uses_optional_api_key(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_API_KEY", " embedding-secret ")

    assert embeddings.get_embeddings_headers() == {
        "Content-Type": "application/json",
        "Authorization": "Bearer embedding-secret",
    }


def test_get_embeddings_headers_omits_empty_api_key(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "  ")

    assert embeddings.get_embeddings_headers() == {"Content-Type": "application/json"}


def test_truncate_utf8_bytes_preserves_valid_utf8():
    text = "abc€é"
    # "abc" = 3 bytes, "€" = 3 bytes, "é" = 2 bytes
    assert embeddings._truncate_utf8_bytes(text, 6) == "abc€"
    assert embeddings._truncate_utf8_bytes(text, 7) == "abc€"
    assert embeddings._truncate_utf8_bytes(text, 8) == "abc€é"


def test_embed_text_uses_llama_cpp_v1_embeddings_endpoint(monkeypatch):
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"data": [{"embedding": [0.1, 0.2]}]}

    def fake_post(
        url: str, headers: dict[str, str], json: dict[str, Any], timeout: int
    ) -> FakeResponse:
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("EMBEDDINGS_API_KEY", "embedding-secret")
    monkeypatch.setattr(embeddings, "EMBEDDINGS_HOST", "http://llama:8080")
    monkeypatch.setattr(embeddings.requests, "post", fake_post)

    output = embeddings.embed_text("123456789")

    assert output == [0.1, 0.2]
    assert captured["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer embedding-secret",
    }
    assert captured["url"] == "http://llama:8080/v1/embeddings"
    assert captured["json"]["input"] == "123456789"
    assert captured["json"] == {
        "model": embeddings.EMBEDDINGS_MODEL,
        "input": "123456789",
        "encoding_format": "float",
    }


def test_embed_text_does_not_duplicate_v1_host_suffix(monkeypatch):
    calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, Any]):
            self.status_code = status_code
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._payload

    def fake_post(
        url: str, headers: dict[str, str], json: dict[str, Any], timeout: int
    ) -> FakeResponse:
        calls.append((url, headers, json))
        return FakeResponse(200, {"data": [{"embedding": [0.9]}]})

    monkeypatch.setenv("EMBEDDINGS_API_KEY", "embedding-secret")
    monkeypatch.setattr(embeddings, "EMBEDDINGS_HOST", "http://llama:8080/v1/")
    monkeypatch.setattr(embeddings.requests, "post", fake_post)

    output = embeddings.embed_text("123456789")

    assert output == [0.9]
    assert calls[0][0] == "http://llama:8080/v1/embeddings"
    assert calls[0][1]["Authorization"] == "Bearer embedding-secret"


def test_embed_text_returns_zero_vector_for_empty(monkeypatch):
    monkeypatch.setattr(embeddings, "EMBED_DIM", 4)
    monkeypatch.setattr(embeddings, "EMBED_MAX_INPUT_TOKENS", 8192)

    output = embeddings.embed_text("   ")

    assert output == [0.0, 0.0, 0.0, 0.0]
