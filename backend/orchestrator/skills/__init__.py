"""
Skills module for Digital Brain.

Agent Skills are a lightweight format for extending AI agent capabilities
with specialized knowledge and workflows. Skills are folders containing
SKILL.md files with YAML frontmatter and Markdown instructions.
"""

from __future__ import annotations

from .loader import load_skill, load_all_skills, Skill, SkillTool
from .matcher import SkillMatcher, SkillMatch
from .registry import SkillRegistry, get_registry
from .runner import SkillScriptRunner, get_runner_for_skill

__all__ = [
    "Skill",
    "SkillTool",
    "load_skill",
    "load_all_skills",
    "SkillMatcher",
    "SkillMatch",
    "SkillRegistry",
    "get_registry",
    "SkillScriptRunner",
    "get_runner_for_skill",
]
