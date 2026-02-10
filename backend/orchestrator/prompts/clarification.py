"""Shared clarification-skill injection helpers for LLM prompts."""

from __future__ import annotations

from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

CLARIFICATION_SKILL_NAME = "clarification-generation"


def get_clarification_skill_prompt_block() -> str | None:
    """
    Return the clarification skill as a system/prompt block, if available.

    The wording intentionally mirrors ACTIVE SKILL formatting used by the
    bounded controller so behavior is consistent across agents.
    """
    try:
        import skills

        registry = skills.get_registry()
        skill = registry.get_skill(CLARIFICATION_SKILL_NAME)
        if not skill:
            return None
        return f"ACTIVE SKILL [{skill.name}] (required):\n{skill.instructions.strip()}"
    except Exception as exc:
        logger.exception("[prompts.clarification] Failed to load clarification skill: %s", exc)
        return None


def append_clarification_skill_to_prompt(prompt: str) -> str:
    """Append clarification skill guidance to a user prompt when available."""
    block = get_clarification_skill_prompt_block()
    if not block:
        return prompt
    return f"{prompt}\n\n{block}"
