"""
Skill registry - central management of loaded skills.

Provides a singleton registry that loads skills on startup and
exposes them for matching and execution.
"""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

from .loader import Skill, load_all_skills
from .matcher import SkillMatcher, SkillMatch


class SkillRegistry:
    """
    Central registry for skill management.

    Handles loading, indexing, and matching of skills.
    Thread-safe for concurrent access.
    """

    def __init__(self, skills_dir: Optional[Path] = None):
        """
        Initialize the skill registry.

        Args:
            skills_dir: Path to skill definitions directory.
                       Defaults to SKILLS_DIR env var or 'skill_definitions'
        """
        self._lock = Lock()
        self._skills: Dict[str, Skill] = {}
        self._matcher: Optional[SkillMatcher] = None
        self._skills_dir = skills_dir

        # Stats tracking
        self._activation_counts: Dict[str, int] = {}

    def load(self, force_reload: bool = False) -> int:
        """
        Load all skills from the skills directory.

        Args:
            force_reload: If True, reload even if already loaded

        Returns:
            Number of skills loaded
        """
        with self._lock:
            if self._skills and not force_reload:
                return len(self._skills)

            skills = load_all_skills(self._skills_dir)
            self._skills = {s.name: s for s in skills}

            # Initialize matcher with loaded skills
            cache_embeddings = os.getenv("SKILLS_CACHE_EMBEDDINGS", "true").lower() == "true"
            self._matcher = SkillMatcher(skills, cache_embeddings=cache_embeddings)

            print(f"[skills.registry] Initialized registry with {len(self._skills)} skills")
            return len(self._skills)

    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        with self._lock:
            return self._skills.get(name)

    def list_skills(self) -> List[Skill]:
        """List all loaded skills."""
        with self._lock:
            return list(self._skills.values())

    def find_matching_skills(
        self,
        query: str,
        max_skills: int = 2,
        min_confidence: float = 0.7,
    ) -> List[SkillMatch]:
        """
        Find skills that match the user query.

        Args:
            query: User's question or request
            max_skills: Maximum number of skills to return
            min_confidence: Minimum confidence threshold

        Returns:
            List of SkillMatch objects
        """
        with self._lock:
            if not self._matcher:
                return []

            matches = self._matcher.find_matching_skills(
                query,
                max_skills=max_skills,
                min_confidence=min_confidence,
            )

            # Track activations
            for match in matches:
                self._activation_counts[match.skill.name] = (
                    self._activation_counts.get(match.skill.name, 0) + 1
                )

            return matches

    def get_skill_index(self) -> str:
        """
        Get the lightweight skill index for system prompt.

        This should always be included in the LLM context.
        """
        with self._lock:
            if not self._matcher:
                return ""
            return self._matcher.get_skill_index()

    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics."""
        with self._lock:
            return {
                "total_skills": len(self._skills),
                "skills": [s.to_index_entry() for s in self._skills.values()],
                "activation_counts": dict(self._activation_counts),
                "embeddings_cached": self._matcher is not None,
            }

    def add_skill(self, skill: Skill) -> None:
        """Add a skill to the registry dynamically."""
        with self._lock:
            self._skills[skill.name] = skill
            if self._matcher:
                self._matcher.add_skill(skill)

    def remove_skill(self, name: str) -> bool:
        """Remove a skill from the registry."""
        with self._lock:
            if name not in self._skills:
                return False
            del self._skills[name]
            if self._matcher:
                self._matcher.remove_skill(name)
            return True


# Global registry instance (singleton)
_registry: Optional[SkillRegistry] = None
_registry_lock = Lock()


def get_registry(skills_dir: Optional[Path] = None) -> SkillRegistry:
    """
    Get the global skill registry instance.

    Creates and initializes the registry on first call.
    Thread-safe singleton pattern.

    Args:
        skills_dir: Path to skill definitions (only used on first call)

    Returns:
        The global SkillRegistry instance
    """
    global _registry

    with _registry_lock:
        if _registry is None:
            _registry = SkillRegistry(skills_dir)
            _registry.load()

        return _registry


def reload_registry() -> int:
    """
    Force reload all skills.

    Returns:
        Number of skills loaded
    """
    global _registry

    with _registry_lock:
        if _registry is None:
            _registry = SkillRegistry()

        return _registry.load(force_reload=True)
