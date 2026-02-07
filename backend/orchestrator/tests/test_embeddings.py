from __future__ import annotations

from typing import Any

import embeddings


def test_truncate_utf8_bytes_preserves_valid_utf8():
    text = "abc€é"
    # "abc" = 3 bytes, "€" = 3 bytes, "é" = 2 bytes
    assert embeddings._truncate_utf8_bytes(text, 6) == "abc€"
    assert embeddings._truncate_utf8_bytes(text, 7) == "abc€"
    assert embeddings._truncate_utf8_bytes(text, 8) == "abc€é"


def test_embed_text_uses_embed_endpoint_with_token_truncate(monkeypatch):
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"embeddings": [[0.1, 0.2]]}

    def fake_post(url: str, json: dict[str, Any], timeout: int) -> FakeResponse:
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(embeddings, "OLLAMA_EMBED_MAX_INPUT_TOKENS", 8192)
    monkeypatch.setattr(embeddings.requests, "post", fake_post)

    output = embeddings.embed_text("123456789")

    assert output == [0.1, 0.2]
    assert captured["url"].endswith("/api/embed")
    assert captured["json"]["input"] == "123456789"
    assert captured["json"]["truncate"] is True
    assert captured["json"]["options"] == {"num_ctx": 8192}


def test_embed_text_falls_back_to_legacy_endpoint(monkeypatch):
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, Any]):
            self.status_code = status_code
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._payload

    def fake_post(url: str, json: dict[str, Any], timeout: int) -> FakeResponse:
        calls.append((url, json))
        if url.endswith("/api/embed"):
            return FakeResponse(404, {})
        return FakeResponse(200, {"embedding": [0.9]})

    monkeypatch.setattr(embeddings, "OLLAMA_EMBED_MAX_INPUT_BYTES", 5)
    monkeypatch.setattr(embeddings.requests, "post", fake_post)

    output = embeddings.embed_text("123456789")

    assert output == [0.9]
    assert calls[0][0].endswith("/api/embed")
    assert calls[1][0].endswith("/api/embeddings")
    assert calls[1][1]["prompt"] == "12345"


def test_embed_text_returns_zero_vector_for_empty(monkeypatch):
    monkeypatch.setattr(embeddings, "EMBED_DIM", 4)
    monkeypatch.setattr(embeddings, "OLLAMA_EMBED_MAX_INPUT_TOKENS", 8192)

    output = embeddings.embed_text("   ")

    assert output == [0.0, 0.0, 0.0, 0.0]
