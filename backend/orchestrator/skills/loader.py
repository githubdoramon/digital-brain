"""
Skill loader - parses SKILL.md and TOOLS.md files.

Skills follow the Agent Skills specification:
- SKILL.md: YAML frontmatter + Markdown instructions
- TOOLS.md: Optional custom tool definitions (YAML)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)


@dataclass
class SkillTool:
    """A custom tool defined by a skill."""

    name: str
    description: str
    script: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class Skill:
    """Represents a loaded skill."""

    name: str
    description: str
    instructions: str
    path: Path
    tools: list[SkillTool] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_scripts(self) -> bool:
        """Check if skill has executable scripts."""
        return len(self.scripts) > 0 or len(self.tools) > 0

    def to_index_entry(self) -> dict[str, Any]:
        """Return minimal info for skill index (always in context)."""
        return {
            "name": self.name,
            "description": self.description,
            "has_scripts": self.has_scripts,
            "tool_count": len(self.tools),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return full skill info."""
        return {
            "name": self.name,
            "description": self.description,
            "instructions": self.instructions,
            "path": str(self.path),
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "script": t.script,
                    "parameters": t.parameters,
                }
                for t in self.tools
            ],
            "scripts": self.scripts,
            "references": self.references,
            "assets": self.assets,
            "metadata": self.metadata,
            "has_scripts": self.has_scripts,
        }


# Regex to parse YAML frontmatter
FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown content."""
    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        return {}, content

    yaml_str = match.group(1)
    body = match.group(2)

    try:
        frontmatter = yaml.safe_load(yaml_str) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML frontmatter: {e}")

    return frontmatter, body.strip()


def _list_files_in_dir(dir_path: Path) -> list[str]:
    """List files in a directory if it exists."""
    if not dir_path.exists() or not dir_path.is_dir():
        return []
    return [f.name for f in dir_path.iterdir() if f.is_file()]


def _load_tools_md(skill_path: Path) -> list[SkillTool]:
    """Load custom tools from TOOLS.md if present."""
    tools_file = skill_path / "TOOLS.md"
    if not tools_file.exists():
        return []

    content = tools_file.read_text(encoding="utf-8")
    frontmatter, _ = _parse_frontmatter(content)

    # If no frontmatter, try parsing entire file as YAML
    if not frontmatter:
        try:
            frontmatter = yaml.safe_load(content) or {}
        except yaml.YAMLError:
            return []

    tools_data = frontmatter.get("tools", [])
    if not isinstance(tools_data, list):
        return []

    tools = []
    for tool_def in tools_data:
        if not isinstance(tool_def, dict):
            continue

        name = tool_def.get("name")
        description = tool_def.get("description")
        script = tool_def.get("script")

        if not name or not description:
            continue

        tools.append(
            SkillTool(
                name=name,
                description=description,
                script=script or "",
                parameters=tool_def.get("parameters", {}),
            )
        )

    return tools


def load_skill(skill_path: Path) -> Skill | None:
    """
    Load a skill from a directory.

    Args:
        skill_path: Path to the skill directory containing SKILL.md

    Returns:
        Skill object if valid, None if invalid or missing required files
    """
    if not skill_path.is_dir():
        return None

    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return None

    try:
        content = skill_md.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("[skills] Failed to read %s: %s", skill_md, e, exc_info=e)
        return None

    try:
        frontmatter, body = _parse_frontmatter(content)
    except ValueError as e:
        logger.warning("[skills] Invalid frontmatter in %s: %s", skill_md, e, exc_info=e)
        return None

    # Required fields
    name = frontmatter.get("name")
    description = frontmatter.get("description")

    if not name:
        logger.warning("[skills] Missing 'name' in %s", skill_md)
        return None

    if not description:
        logger.warning("[skills] Missing 'description' in %s", skill_md)
        return None

    # Validate name format (lowercase, hyphens only)
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name):
        logger.warning("[skills] Invalid name format '%s' in %s", name, skill_md)
        return None

    # Load optional components
    tools = _load_tools_md(skill_path)
    scripts = _list_files_in_dir(skill_path / "scripts")
    references = _list_files_in_dir(skill_path / "references")
    assets = _list_files_in_dir(skill_path / "assets")

    # Extract optional metadata
    metadata = {k: v for k, v in frontmatter.items() if k not in ("name", "description")}

    return Skill(
        name=name,
        description=description,
        instructions=body,
        path=skill_path,
        tools=tools,
        scripts=scripts,
        references=references,
        assets=assets,
        metadata=metadata,
    )


def load_all_skills(skills_dir: Path | None = None) -> list[Skill]:
    """
    Load all skills from a directory.

    Args:
        skills_dir: Path to directory containing skill folders.
                   Defaults to SKILLS_DIR env var or 'skill_definitions'

    Returns:
        List of loaded Skill objects
    """
    if skills_dir is None:
        base_dir = Path(__file__).parent.parent
        dir_name = os.getenv("SKILLS_DIR", "skill_definitions")
        skills_dir = base_dir / dir_name

    if not skills_dir.exists():
        logger.warning("[skills] Skills directory not found: %s", skills_dir)
        return []

    skills = []
    for item in skills_dir.iterdir():
        if not item.is_dir():
            continue

        # Skip hidden directories
        if item.name.startswith("."):
            continue

        skill = load_skill(item)
        if skill:
            logger.info("[skills] Loaded skill: %s", skill.name)
            skills.append(skill)

    logger.info("[skills] Loaded %s skills from %s", len(skills), skills_dir)
    return skills
