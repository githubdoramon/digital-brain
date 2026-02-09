from __future__ import annotations

import json
import os
from collections.abc import Iterable, Sequence
from typing import Literal

from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

MAX_LABEL_PROMPT_CHARS = int(os.getenv("DOCUMENT_LABEL_PROMPT_CHARS", "10000"))
MAX_SUGGESTED_TAGS = int(os.getenv("DOCUMENT_LABEL_MAX_COUNT", "5"))
LLM_CHAT_MODEL = os.getenv("LLM_CHAT_MODEL")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))

MAJOR_TAGS = [
    "Finance",
    "Health",
    "Living",
    "Work",
    "Education",
    "Legal",
    "Family",
    "Personal",
]
MAJOR_TAG_KEYWORDS = {
    "Finance": [
        "bank",
        "payment",
        "invoice",
        "receipt",
        "tax",
        "loan",
        "mortgage",
        "salary",
        "payroll",
        "budget",
        "insurance",
        "subscription",
        "investment",
        "financ",
    ],
    "Health": [
        "doctor",
        "clinic",
        "hospital",
        "medical",
        "prescription",
        "therapy",
        "vaccine",
        "fitness",
        "lab",
        "blood",
        "insurance claim",
        "health",
    ],
    "Living": [
        "rent",
        "lease",
        "apartment",
        "house",
        "home",
        "utility",
        "electricity",
        "water",
        "internet",
        "repair",
        "maintenance",
        "pet",
    ],
    "Work": [
        "job",
        "employer",
        "manager",
        "coworker",
        "project",
        "meeting",
        "company",
        "work",
        "professional",
        "career",
        "visa",
        "resume",
    ],
    "Education": [
        "school",
        "university",
        "college",
        "degree",
        "diploma",
        "course",
        "class",
        "training",
        "transcript",
        "certificate",
        "learning",
    ],
    "Legal": [
        "contract",
        "agreement",
        "license",
        "permit",
        "law",
        "court",
        "id",
        "passport",
        "immigration",
        "visa",
        "legal",
    ],
    "Family": [
        "family",
        "spouse",
        "partner",
        "child",
        "parent",
        "caregiver",
        "marriage",
        "inheritance",
        "relative",
        "emergency contact",
    ],
    "Personal": [
        "travel",
        "vacation",
        "trip",
        "hobby",
        "journal",
        "goal",
        "plan",
        "ticket",
        "concert",
        "event",
        "personal",
    ],
}


def _call_llm_text(
    prompt: str,
    *,
    system_prompt: str,
    timeout: int,
) -> str:
    from llm_helpers import call_llm

    return call_llm(
        prompt,
        system_prompt=system_prompt,
        model=LLM_CHAT_MODEL,
        timeout=timeout,
    )


def _has_major_tag(tags: Sequence[str]) -> bool:
    lowered = {
        str(tag).strip().lower() for tag in tags if isinstance(tag, str) and str(tag).strip()
    }
    return any(major.lower() in lowered for major in MAJOR_TAGS)


def _needs_additional_tags(tags: Sequence[str]) -> bool:
    normalized = _normalize_strings(tags)
    return (len(normalized) < 4) or (not _has_major_tag(normalized))


def _normalize_strings(values: Iterable[str] | None) -> list[str]:
    if not values:
        return []
    seen = set()
    normalized: list[str] = []
    for item in values:
        if item is None:
            continue
        candidate = str(item).strip()
        if not candidate:
            continue
        lower = candidate.lower()
        if lower in seen:
            continue
        seen.add(lower)
        normalized.append(candidate)
    return normalized


def _parse_suggested_tags_response(raw_content: str) -> list[str]:
    try:
        loaded = json.loads(raw_content)
        if isinstance(loaded, dict):
            if "tags" in loaded:
                candidate = loaded["tags"]
            elif "labels" in loaded:
                candidate = loaded["labels"]
            else:
                candidate = loaded
        else:
            candidate = loaded
    except json.JSONDecodeError:
        lines = [line.strip("-• ").strip() for line in raw_content.splitlines()]
        candidate = [line for line in lines if line]

    parsed_tags: list[str] = []
    if isinstance(candidate, dict):
        candidate = list(candidate.values())
    if isinstance(candidate, list):
        for item in candidate:
            if isinstance(item, str):
                label = item.strip()
                if label:
                    parsed_tags.append(label)
    return parsed_tags


def _suggest_tags(
    content: str,
    tags: Sequence[str],
    subject: Literal["document", "event"],
) -> list[str]:
    cleaned = (content or "").strip()
    if not cleaned or not LLM_CHAT_MODEL:
        return []

    prompt_content = cleaned[:MAX_LABEL_PROMPT_CHARS]
    existing = ", ".join(tags) if tags else "none"
    major_categories = "; ".join(MAJOR_TAGS)
    subject_excerpt_label = "Event context" if subject == "event" else "Document excerpt"

    subject_instruction = (
        "Consider who is involved, what happened, where, and the outcome. "
        "Capture medium (meeting, call, email), purpose (status update, decision, planning), "
        "and any workstream or project hints."
        if subject == "event"
        else "Consider the document type (contract, receipt, medical record, ID, notes) and topic."
    )

    subtag_examples = "; ".join(
        f"{major}: {', '.join(MAJOR_TAG_KEYWORDS.get(major, [])[:3])}" for major in MAJOR_TAGS
    )

    system_prompt = (
        "You are a tagging assistant for a personal knowledge base. "
        "Propose concise English tags (1-3 words) that balance specificity and generality. "
        f"Always include AT LEAST ONE of these major categories as a tag exactly as written: {major_categories}. "
        f"{subject_instruction} "
        'Blend specific tags (e.g., "Form 1040", "Dr. Smith", "Project Apollo") with broader ones (e.g., "Taxes", "Travel", "Contracts", "Blood test"). '
        f"Here are example sub-tags for each major category: {subtag_examples}. "
        'Respond ONLY with JSON in the shape {"tags": ["tag", ...]} and do not include any prose or numbering.'
    )
    user_prompt = (
        f"Existing tags: {existing}\n"
        f"Major categories (must include at least one): {major_categories}\n"
        f"{subject_excerpt_label}:\n"
        f"{prompt_content}\n\n"
        f"Return up to {MAX_SUGGESTED_TAGS} new tags relevant to this {subject}."
    )

    try:
        raw_content = _call_llm_text(
            user_prompt,
            system_prompt=system_prompt,
            timeout=OLLAMA_TIMEOUT,
        ).strip()
        if not raw_content:
            return []
        parsed = _parse_suggested_tags_response(raw_content)
        return parsed[:MAX_SUGGESTED_TAGS]
    except Exception as exc:
        logger.warning("[tags_manager] Failed to generate tags: %s", exc, exc_info=exc)
        return []


def _suggest_additional_tags(content: str, tags: Sequence[str]) -> list[str]:
    if not _needs_additional_tags(tags):
        return []
    return _suggest_tags(content, tags, "document")


def _suggest_event_tags(
    title: str | None,
    summary: str | None,
    tags: Sequence[str],
    *,
    types: Sequence[str] | None = None,
) -> list[str]:
    if not _needs_additional_tags(tags):
        return []
    combined_parts = [part.strip() for part in (title or "", summary or "") if part]
    combined_content = "\n".join(combined_parts)
    return _suggest_tags(
        combined_content or "Event information unavailable",
        tags,
        subject="event",
    )


def _merge_tag_lists(primary: Sequence[str], secondary: Sequence[str]) -> list[str]:
    merged: list[str] = list(primary or [])
    seen = {tag.lower() for tag in merged if isinstance(tag, str)}
    for tag in secondary:
        if not isinstance(tag, str):
            continue
        candidate = tag.strip()
        if not candidate:
            continue
        lowered = candidate.lower()
        if lowered in seen:
            continue
        merged.append(candidate)
        seen.add(lowered)
    return merged


def get_tag_taxonomy() -> dict[str, list[str]]:
    """
    Return the tag taxonomy as a dict mapping major tags to their minor tags (keywords).

    Used by the LLM prompt builder to provide context about available tags.
    """
    return {major: list(keywords) for major, keywords in MAJOR_TAG_KEYWORDS.items()}
