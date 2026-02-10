from __future__ import annotations

import sys
from types import SimpleNamespace

from prompts.clarification import (
    append_clarification_skill_to_prompt,
    get_clarification_skill_prompt_block,
)


class _FakeRegistry:
    def __init__(self, skill):
        self._skill = skill

    def get_skill(self, _name: str):
        return self._skill


def test_get_clarification_skill_prompt_block(monkeypatch):
    fake_skill = SimpleNamespace(
        name="clarification-generation",
        instructions="Ask only for missing details.",
    )

    fake_module = SimpleNamespace(get_registry=lambda: _FakeRegistry(fake_skill))
    monkeypatch.setitem(sys.modules, "skills", fake_module)

    block = get_clarification_skill_prompt_block()
    assert block is not None
    assert "ACTIVE SKILL [clarification-generation]" in block


def test_append_clarification_skill_to_prompt_without_skill(monkeypatch):
    fake_module = SimpleNamespace(get_registry=lambda: _FakeRegistry(None))
    monkeypatch.setitem(sys.modules, "skills", fake_module)

    prompt = "Base prompt"
    assert append_clarification_skill_to_prompt(prompt) == prompt
