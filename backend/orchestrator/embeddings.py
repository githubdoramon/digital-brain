from __future__ import annotations

import os
from typing import Any

import requests

from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

EMBEDDINGS_HOST = os.getenv("EMBEDDINGS_HOST", "http://localhost:8080")
EMBEDDINGS_MODEL = os.getenv("EMBEDDINGS_MODEL", "nomic-embed-text")
EMBEDDINGS_API_KEY = os.getenv("EMBEDDINGS_API_KEY", "")
EMBED_DIM = int(os.getenv("EMBEDDINGS_DIM", "768"))
EMBED_MAX_INPUT_TOKENS = int(os.getenv("EMBEDDINGS_MAX_INPUT_TOKENS", "2048"))
EMBED_MAX_INPUT_BYTES = int(os.getenv("EMBEDDINGS_MAX_INPUT_BYTES", "3000"))
ADAPTIVE_MIN_INPUT_BYTES = 800
_adaptive_max_input_bytes = max(ADAPTIVE_MIN_INPUT_BYTES, EMBED_MAX_INPUT_BYTES)


def get_embeddings_headers() -> dict[str, str]:
    """Return standard headers for embedding API requests."""
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("EMBEDDINGS_API_KEY", EMBEDDINGS_API_KEY).strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _get_embeddings_base_url() -> str:
    host = EMBEDDINGS_HOST.rstrip("/")
    return host if host.endswith("/v1") else f"{host}/v1"


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
        or ("context length" in normalized
        and "exceeds" in normalized)
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

    # llama.cpp's OpenAI-compatible endpoint returns {"data": [{"embedding": [...]}]}.
    response_data = data.get("data")
    if isinstance(response_data, list) and response_data:
        first_result = response_data[0]
        if isinstance(first_result, dict):
            embedding = first_result.get("embedding")
            if isinstance(embedding, list) and embedding:
                return [float(value) for value in embedding]
    return None


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
            f"{_get_embeddings_base_url()}/embeddings",
            headers=get_embeddings_headers(),
            json={
                "model": EMBEDDINGS_MODEL,
                "input": payload_text,
                "encoding_format": "float",
            },
            timeout=30,
        )

    response = _post_embed(input_text)

    if response.status_code >= 400:
        body = (response.text or "").strip()
        logger.warning(
            "[embeddings] embed request failed status=%s chars=%s bytes=%s body=%s",
            response.status_code,
            input_chars,
            input_bytes,
            body[:300],
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
                logger.info(
                    "[embeddings] retrying after context error chars=%s bytes=%s",
                    reduced_chars,
                    reduced_bytes,
                )
                retry_response = _post_embed(reduced)
                if retry_response.status_code >= 400:
                    retry_body = (retry_response.text or "").strip()
                    logger.warning(
                        "[embeddings] retry failed status=%s body=%s",
                        retry_response.status_code,
                        retry_body[:300],
                    )
                    continue
                retry_data = retry_response.json()
                retry_embedding = _extract_embedding(retry_data)
                if retry_embedding:
                    if reduced_bytes < _adaptive_max_input_bytes:
                        _adaptive_max_input_bytes = max(ADAPTIVE_MIN_INPUT_BYTES, reduced_bytes)
                        logger.info(
                            "[embeddings] adapting byte cap after successful retry new_byte_cap=%s",
                            _adaptive_max_input_bytes,
                        )
                    return retry_embedding
        response.raise_for_status()

    data = response.json()
    embedding = _extract_embedding(data)
    if embedding:
        return embedding
    raise RuntimeError(f"Embedding response missing data: {data}")
