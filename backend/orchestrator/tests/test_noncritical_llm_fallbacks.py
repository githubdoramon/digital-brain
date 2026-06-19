from __future__ import annotations

import os

os.environ.setdefault("LLM_BASE_URL", "http://localhost:11434/v1")
os.environ.setdefault("LLM_CHAT_MODEL_FAST", "test-fast")
os.environ.setdefault("LLM_CHAT_MODEL_SMART", "test-smart")

import documents
import llm
import tags_manager
from llm_helpers import LLMUnavailableError
from tools.handlers import memory


def test_document_title_falls_back_when_llm_unavailable(monkeypatch):
    monkeypatch.setattr(
        documents,
        "_call_llm_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LLMUnavailableError("LLM service is unavailable")
        ),
    )

    assert documents._suggest_title("content", fallback="receipt_2024.pdf") == "Receipt 2024"


def test_document_summary_returns_none_when_llm_unavailable(monkeypatch):
    monkeypatch.setattr(
        documents,
        "_call_llm_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LLMUnavailableError("LLM service is unavailable")
        ),
    )

    assert documents._summarize_description("important content") is None


def test_document_tag_translation_keeps_original_tags_when_llm_unavailable(monkeypatch):
    monkeypatch.setattr(
        documents,
        "_call_llm_json_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LLMUnavailableError("LLM service is unavailable")
        ),
    )

    assert documents._translate_tags_to_english(["factura", "salud"]) == ["factura", "salud"]


def test_document_date_inference_returns_none_when_llm_unavailable(monkeypatch):
    monkeypatch.setattr(
        documents,
        "_call_llm_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LLMUnavailableError("LLM service is unavailable")
        ),
    )

    assert documents._suggest_document_date("dated text", fallback=None) is None


def test_tag_suggestion_returns_empty_when_llm_unavailable(monkeypatch):
    monkeypatch.setattr(
        tags_manager,
        "_call_llm_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LLMUnavailableError("LLM service is unavailable")
        ),
    )

    assert tags_manager._suggest_tags("met with team", [], "event") == []


def test_tag_response_parser_rejects_stringified_json_fragments():
    raw = json.dumps(
        {
            "tags": [
                "Microsoft Teams",
                "Status update",
                "[\n",
                '"Work",\n',
                '{"Work"',
                '"Microsoft Teams"',
                "Meeting",
            ]
        }
    )

    assert tags_manager._parse_suggested_tags_response(raw) == [
        "Microsoft Teams",
        "Status update",
        "Meeting",
    ]


def test_tag_list_merge_sanitizes_json_fragments():
    assert tags_manager._merge_tag_lists(
        ["Work", "```json", '{"tags": ["Family"]}'],
        ["Meeting", '"tags": [', "Work"],
    ) == ["Work", "Meeting"]


def test_thread_title_generation_returns_none_when_llm_unavailable(monkeypatch):
    monkeypatch.setattr(
        "llm_helpers.call_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LLMUnavailableError("LLM service is unavailable")
        ),
    )

    assert llm._generate_thread_title("How did my week go?") is None


def test_memory_summary_uses_fallback_when_llm_unavailable(monkeypatch):
    monkeypatch.setattr(
        "llm_helpers.call_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LLMUnavailableError("LLM service is unavailable")
        ),
    )

    result = memory._synthesize_memory_summary(
        question="What happened?",
        focus="summary",
        tags=[],
        event_types=[],
        events=[{"title": "Standup", "tags": ["work"]}],
        documents=[{"title": "Notes", "tags": ["work"]}],
        inspected_documents=[{"title": "Notes", "tags": ["work"]}],
    )

    assert "reviewed 1 events and 1 documents" in result
