from __future__ import annotations

import os
from typing import Any

import requests

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = int(os.getenv("OLLAMA_EMBED_DIM", "768"))
OLLAMA_EMBED_MAX_INPUT_TOKENS = int(os.getenv("OLLAMA_EMBED_MAX_INPUT_TOKENS", "2048"))
OLLAMA_EMBED_MAX_INPUT_BYTES = int(os.getenv("OLLAMA_EMBED_MAX_INPUT_BYTES", "3000"))
ADAPTIVE_MIN_INPUT_BYTES = 800
_adaptive_max_input_bytes = max(ADAPTIVE_MIN_INPUT_BYTES, OLLAMA_EMBED_MAX_INPUT_BYTES)


def _truncate_utf8_bytes(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _is_context_length_error(message: str) -> bool:
    normalized = (message or "").lower()
    return (
        "input length exceeds the context length" in normalized
        or "exceeds the context length" in normalized
        or "context length" in normalized and "exceeds" in normalized
    )


def _extract_embedding(data: dict[str, Any]) -> list[float] | None:
    embeddings = data.get("embeddings")
    if isinstance(embeddings, list) and embeddings:
        first = embeddings[0]
        if isinstance(first, list):
            return [float(value) for value in first]
        if isinstance(first, (int, float)):
            return [float(value) for value in embeddings]

    legacy_embedding = data.get("embedding")
    if isinstance(legacy_embedding, list) and legacy_embedding:
        return [float(value) for value in legacy_embedding]
    return None


def _embed_text_with_legacy_endpoint(text: str) -> list[float]:
    fallback_text = _truncate_utf8_bytes(text, OLLAMA_EMBED_MAX_INPUT_BYTES)
    if not fallback_text:
        return [0.0] * EMBED_DIM

    response = requests.post(
        f"{OLLAMA_HOST}/api/embeddings",
        json={"model": OLLAMA_EMBED_MODEL, "prompt": fallback_text},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    embedding = _extract_embedding(data)
    if embedding:
        return embedding
    raise RuntimeError(f"Ollama embeddings response missing data: {data}")


def embed_text(text: str) -> list[float]:
    global _adaptive_max_input_bytes

    text = (text or "").strip()
    if not text:
        return [0.0] * EMBED_DIM
    active_byte_cap = max(ADAPTIVE_MIN_INPUT_BYTES, _adaptive_max_input_bytes)
    input_text = _truncate_utf8_bytes(text, active_byte_cap)
    input_chars = len(input_text)
    input_bytes = len(input_text.encode("utf-8"))

    def _post_embed(payload_text: str) -> requests.Response:
        return requests.post(
            f"{OLLAMA_HOST}/api/embed",
            json={
                "model": OLLAMA_EMBED_MODEL,
                "input": payload_text,
                "truncate": True,
                "options": {"num_ctx": OLLAMA_EMBED_MAX_INPUT_TOKENS},
            },
            timeout=30,
        )

    response = _post_embed(input_text)

    if response.status_code == 404:
        print("[embeddings] /api/embed not available; using legacy /api/embeddings")
        return _embed_text_with_legacy_endpoint(input_text)

    if response.status_code >= 400:
        body = (response.text or "").strip()
        print(
            "[embeddings] embed request failed "
            f"status={response.status_code} chars={input_chars} bytes={input_bytes} body={body[:300]}"
        )
        if _is_context_length_error(body):
            retry_caps = [2500, 2000, 1600, 1200, 1000, 800]
            for cap in retry_caps:
                if input_bytes <= cap:
                    continue
                reduced = _truncate_utf8_bytes(input_text, cap)
                if not reduced:
                    continue
                reduced_chars = len(reduced)
                reduced_bytes = len(reduced.encode("utf-8"))
                print(
                    "[embeddings] retrying after context error "
                    f"chars={reduced_chars} bytes={reduced_bytes}"
                )
                retry_response = _post_embed(reduced)
                if retry_response.status_code == 404:
                    _adaptive_max_input_bytes = min(_adaptive_max_input_bytes, reduced_bytes)
                    return _embed_text_with_legacy_endpoint(reduced)
                if retry_response.status_code >= 400:
                    retry_body = (retry_response.text or "").strip()
                    print(
                        "[embeddings] retry failed "
                        f"status={retry_response.status_code} body={retry_body[:300]}"
                    )
                    continue
                retry_data = retry_response.json()
                retry_embedding = _extract_embedding(retry_data)
                if retry_embedding:
                    if reduced_bytes < _adaptive_max_input_bytes:
                        _adaptive_max_input_bytes = max(ADAPTIVE_MIN_INPUT_BYTES, reduced_bytes)
                        print(
                            "[embeddings] adapting byte cap after successful retry "
                            f"new_byte_cap={_adaptive_max_input_bytes}"
                        )
                    return retry_embedding
            return _embed_text_with_legacy_endpoint(input_text)
        response.raise_for_status()

    data = response.json()
    embedding = _extract_embedding(data)
    if embedding:
        return embedding
    raise RuntimeError(f"Ollama embed response missing data: {data}")
