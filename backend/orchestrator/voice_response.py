"""Voice-response modality contract and deterministic output guardrails."""

from __future__ import annotations

import asyncio
import re
from enum import Enum


class ResponseModality(str, Enum):
    TEXT = "text"
    VOICE = "voice"


def normalize_modality(value: str | ResponseModality | None) -> ResponseModality:
    if isinstance(value, ResponseModality):
        return value
    try:
        return ResponseModality(str(value or ResponseModality.TEXT).strip().lower())
    except ValueError:
        return ResponseModality.TEXT


def validate_voice_answer(answer: str, *, max_words: int = 48) -> list[str]:
    """Return contract violations without mutating the candidate answer."""
    text = str(answer or "").strip()
    violations: list[str] = []
    if not text:
        violations.append("empty")
        return violations
    if len(re.findall(r"\b[\w'’-]+\b", text)) > max_words:
        violations.append("too_many_words")
    if len(re.findall(r"(?<=[.!?])\s+", text)) + 1 > 2:
        violations.append("too_many_sentences")
    if re.search(r"https?://|www\.", text, flags=re.IGNORECASE):
        violations.append("url")
    if re.search(r"[`*_~>#|]|\[[^\]]+\]\([^)]*\)", text):
        violations.append("markdown")
    if re.search(r"(?:^|\n)\s*(?:[-*•]\s+|\d+[.)]\s+)", text):
        violations.append("list")
    if re.search(
        r"\b(?:contact|event|document|place|thread|message):\s*[A-Za-z0-9_-]+\b"
        r"|\b(?:id|uuid)\s*[:#]?\s*[A-Za-z0-9_-]{8,}\b"
        r"|\b[0-9a-f]{8}-[0-9a-f-]{27,}\b",
        text,
        flags=re.IGNORECASE,
    ):
        violations.append("raw_id")
    return violations


def sanitize_voice_answer(answer: str, *, max_sentences: int = 2, max_words: int = 48) -> str:
    """Make model output speakable while preserving its first useful content."""
    text = str(answer or "").strip()
    text = re.sub(r"```(?:[^\n]*)\n?|```", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_~>#`|]", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    if not text:
        return "I could not complete that request."
    # Keep a short spoken response even if a provider ignores the prompt.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    text = " ".join(sentences[:max_sentences]).strip()
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words]).rstrip(" ,;:") + "."
    if validate_voice_answer(text, max_words=max_words):
        return "I could not complete that request. Please ask again."
    return text


def prepare_voice_answer_sync(answer: str, *, repair_timeout_seconds: int = 8) -> str:
    """Validate and, at most once, repair a voice answer before persistence."""
    candidate = str(answer or "").strip()
    violations = validate_voice_answer(candidate)
    if not violations:
        return candidate

    try:
        from llm_helpers import build_json_schema_response_format, call_llm_json
        from llm_json_schemas import GLASSES_VOICE_ANSWER_SCHEMA

        schema = GLASSES_VOICE_ANSWER_SCHEMA
        prompt = (
            "Rewrite the candidate answer for spoken English. Preserve its meaning and facts. "
            "Return at most 48 words and two short sentences. Use plain text only: no Markdown, "
            "URLs, citations, lists, or internal IDs. Candidate:\n" + candidate
        )
        repaired = call_llm_json(
            prompt,
            system_prompt=voice_system_instruction(),
            use_fast_model=True,
            timeout=max(1, repair_timeout_seconds),
            max_tokens=100,
            temperature=0,
            response_format=build_json_schema_response_format(
                name="glasses_voice_answer",
                schema=schema,
            ),
        )
        repaired_text = repaired.get("answer") if isinstance(repaired, dict) else None
        if isinstance(repaired_text, str) and not validate_voice_answer(repaired_text):
            return repaired_text.strip()
    except asyncio.CancelledError:
        raise
    except Exception:
        # A deterministic fallback keeps the endpoint bounded when the repair
        # model is unavailable; it is still validated before persistence.
        pass

    sanitized = sanitize_voice_answer(candidate)
    if validate_voice_answer(sanitized):
        return "I could not complete that request. Please ask again."
    return sanitized


async def prepare_voice_answer(answer: str, *, repair_timeout_seconds: int = 8) -> str:
    """Async wrapper that keeps synchronous provider calls off the event loop."""
    return await asyncio.wait_for(
        asyncio.to_thread(
            prepare_voice_answer_sync,
            answer,
            repair_timeout_seconds=repair_timeout_seconds,
        ),
        timeout=max(1, repair_timeout_seconds) + 1,
    )


def voice_system_instruction() -> str:
    return (
        "VOICE RESPONSE MODE: Reply in plain spoken English. Use at most two short sentences "
        "(about 20 seconds). Do not use Markdown, tables, URLs, citations, raw IDs, or long lists. "
        "Give the answer briefly and offer more detail if useful. Clarification questions must "
        "also be speakable."
    )
