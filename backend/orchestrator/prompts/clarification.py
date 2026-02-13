"""Shared clarification guidelines for LLM prompts."""

from __future__ import annotations

_CLARIFICATION_GUIDANCE = (
    "CLARIFICATION GUIDELINES:\n"
    "- Ask only for information that is still missing — never re-ask known data.\n"
    "- Use the smallest number of follow-up fields needed to proceed.\n"
    "- For entity/people ambiguity, disambiguate directly — avoid unrelated fields.\n"
    "- Prefer concrete field kinds: datetime for timestamps, text for places, textarea for free-form, select for choices.\n"
    "- Avoid broad 'tell me more' prompts, decorative fields, or duplicate fields for the same fact.\n"
    "- When emitting clarification via emit_ui_directive, always include a clear fallback_text."
)


def get_clarification_guidelines() -> str | None:
    """Return inline clarification guidance as a prompt block."""
    return _CLARIFICATION_GUIDANCE


def append_clarification_guidelines(prompt: str) -> str:
    """Append clarification guidance to a prompt string."""
    block = get_clarification_guidelines()
    if not block:
        return prompt
    return f"{prompt}\n\n{block}"
