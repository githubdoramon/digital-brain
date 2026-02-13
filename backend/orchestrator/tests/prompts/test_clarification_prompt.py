from __future__ import annotations

from prompts.clarification import (
    append_clarification_guidelines,
    get_clarification_guidelines,
)


def test_get_clarification_guidelines():
    block = get_clarification_guidelines()
    assert block is not None
    assert "CLARIFICATION GUIDELINES" in block
    assert "smallest number" in block


def test_append_clarification_guidelines():
    prompt = "Base prompt"
    result = append_clarification_guidelines(prompt)
    assert result.startswith("Base prompt")
    assert "CLARIFICATION GUIDELINES" in result
