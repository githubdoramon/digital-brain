from __future__ import annotations

from typing import Any

import documents


def test_generate_document_embedding_translates_content_once_and_caches(monkeypatch):
    raw_metadata: dict[str, Any] = {}
    translate_calls: list[tuple[str, int]] = []
    captured: dict[str, str] = {}

    def fake_translate(text: str, max_chars: int) -> str:
        translate_calls.append((text, max_chars))
        return "hello world"

    def fake_embed(text: str) -> list[float]:
        captured["text"] = text
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(documents, "_translate_text_to_english", fake_translate)
    monkeypatch.setattr(documents, "embed_text", fake_embed)

    embedding = documents._generate_document_embedding(
        {"content": "Hola mundo"},
        raw_metadata=raw_metadata,
    )

    assert embedding == [0.1, 0.2, 0.3]
    expected_chars = min(documents.MAX_EMBED_CHARS, documents.MAX_TRANSLATION_SOURCE_CHARS)
    assert translate_calls == [("Hola mundo", expected_chars)]
    assert captured["text"] == "hello world"
    assert raw_metadata[documents.CONTENT_TRANSLATION_TEXT_KEY] == "hello world"
    assert raw_metadata[documents.CONTENT_TRANSLATION_HASH_KEY] == documents._translation_source_hash(
        "Hola mundo"
    )
    assert raw_metadata[documents.CONTENT_TRANSLATION_GENERATED_KEY] is True


def test_generate_document_embedding_uses_cached_translation(monkeypatch):
    source_text = "Hola mundo"
    raw_metadata = {
        documents.CONTENT_TRANSLATION_TEXT_KEY: "hello world",
        documents.CONTENT_TRANSLATION_HASH_KEY: documents._translation_source_hash(source_text),
        documents.CONTENT_TRANSLATION_GENERATED_KEY: True,
    }
    captured: dict[str, str] = {}

    def should_not_translate(_text: str, _max_chars: int) -> str:
        raise AssertionError("translation should not be called when cached translation is valid")

    def fake_embed(text: str) -> list[float]:
        captured["text"] = text
        return [0.9]

    monkeypatch.setattr(documents, "_translate_text_to_english", should_not_translate)
    monkeypatch.setattr(documents, "embed_text", fake_embed)

    embedding = documents._generate_document_embedding(
        {"content": source_text},
        raw_metadata=raw_metadata,
    )

    assert embedding == [0.9]
    assert captured["text"] == "hello world"


def test_translate_text_to_english_uses_full_excerpt(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_call(prompt: str, *, system_prompt: str, timeout: int) -> str:
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        captured["timeout"] = timeout
        return "translated"

    monkeypatch.setattr(documents, "LLM_CHAT_MODEL", "test-model")
    monkeypatch.setattr(documents, "_call_llm_text", fake_call)

    long_text = "x" * 250
    translated = documents._translate_text_to_english(long_text, 200)

    assert translated == "translated"
    assert captured["prompt"] == long_text[:200]
    assert "Translate the user's text into fluent English" in captured["system_prompt"]


def test_translate_text_to_english_uses_llm_chat_model(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_call(prompt: str, *, system_prompt: str, timeout: int) -> str:
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        captured["timeout"] = timeout
        return "translated"

    monkeypatch.setattr(documents, "LLM_CHAT_MODEL", "fallback-llm-model")
    monkeypatch.setattr(documents, "_call_llm_text", fake_call)

    translated = documents._translate_text_to_english("Olá mundo", 200)

    assert translated == "translated"
    assert captured["prompt"] == "Olá mundo"
