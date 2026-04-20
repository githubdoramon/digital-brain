"""
Skills module for Digital Brain.

Agent Skills are a lightweight format for extending AI agent capabilities
with specialized knowledge and workflows. Skills are folders containing
SKILL.md files with YAML frontmatter and Markdown instructions.
"""

from __future__ import annotations

from .loader import Skill, SkillTool, load_all_skills, load_skill
from .matcher import SkillMatch, SkillMatcher
from .registry import SkillRegistry, get_registry
from .runner import SkillScriptRunner, get_runner_for_skill

__all__ = [
    "Skill",
    "SkillMatch",
    "SkillMatcher",
    "SkillRegistry",
    "SkillScriptRunner",
    "SkillTool",
    "get_registry",
    "get_runner_for_skill",
    "load_all_skills",
    "load_skill",
]
