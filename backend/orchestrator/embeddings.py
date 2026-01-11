from __future__ import annotations

import os

import requests

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = int(os.getenv("OLLAMA_EMBED_DIM", "768"))


def embed_text(text: str) -> list[float]:
    text = (text or "").strip()
    if not text:
        return [0.0] * EMBED_DIM
    resp = requests.post(
        f"{OLLAMA_HOST}/api/embeddings",
        json={"model": OLLAMA_EMBED_MODEL, "prompt": text},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    emb = data.get("embedding")
    if not emb:
        raise RuntimeError(f"Ollama embeddings response missing data: {data}")
    return emb
