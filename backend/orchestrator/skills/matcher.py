"""
Skill matcher - finds relevant skills using vector similarity.

Uses embeddings to match user queries to skill descriptions,
enabling automatic skill selection without loading all skills into context.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .loader import Skill

# Import from parent package
import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])
import embeddings as embeddings_module


@dataclass
class SkillMatch:
    """Result of matching a query to a skill."""
    skill: Skill
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_name": self.skill.name,
            "description": self.skill.description,
            "confidence": self.confidence,
            "has_scripts": self.skill.has_scripts,
        }


def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(vec_a) != len(vec_b):
        return 0.0

    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


class SkillMatcher:
    """
    Matches user queries to relevant skills using vector similarity.

    Embeddings are computed lazily and cached for efficiency.
    """

    def __init__(
        self,
        skills: List[Skill],
        cache_embeddings: bool = True,
    ):
        """
        Initialize the skill matcher.

        Args:
            skills: List of skills to match against
            cache_embeddings: Whether to cache skill embeddings (default True)
        """
        self.skills = {s.name: s for s in skills}
        self.cache_embeddings = cache_embeddings
        self._embeddings_cache: Dict[str, List[float]] = {}

        # Pre-compute embeddings if caching is enabled
        if cache_embeddings and skills:
            self._precompute_embeddings()

    def _precompute_embeddings(self) -> None:
        """Pre-compute and cache embeddings for all skill descriptions."""
        print(f"[skills.matcher] Pre-computing embeddings for {len(self.skills)} skills...")

        for name, skill in self.skills.items():
            if name not in self._embeddings_cache:
                # Combine name and description for better matching
                text = f"{skill.name}: {skill.description}"
                try:
                    embedding = embeddings_module.embed_text(text)
                    self._embeddings_cache[name] = embedding
                except Exception as e:
                    print(f"[skills.matcher] Failed to embed skill '{name}': {e}")

        print(f"[skills.matcher] Cached {len(self._embeddings_cache)} skill embeddings")

    def _get_skill_embedding(self, skill: Skill) -> Optional[List[float]]:
        """Get embedding for a skill (from cache or compute)."""
        if skill.name in self._embeddings_cache:
            return self._embeddings_cache[skill.name]

        # Compute embedding if not cached
        text = f"{skill.name}: {skill.description}"
        try:
            embedding = embeddings_module.embed_text(text)
            if self.cache_embeddings:
                self._embeddings_cache[skill.name] = embedding
            return embedding
        except Exception as e:
            print(f"[skills.matcher] Failed to embed skill '{skill.name}': {e}")
            return None

    def _get_query_embedding(self, query: str) -> Optional[List[float]]:
        """Get embedding for a user query."""
        try:
            return embeddings_module.embed_text(query)
        except Exception as e:
            print(f"[skills.matcher] Failed to embed query: {e}")
            return None
    
    def _build_context_query(
        self,
        query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """
        Build a context-aware query string from conversation history.
        
        Args:
            query: The current user query
            conversation_history: Optional list of previous messages
            
        Returns:
            A formatted string combining recent history and current query
        """
        context_parts = []
        
        if conversation_history:
            # Include last few messages for context (limit to avoid too long queries)
            recent_history = conversation_history[-5:]  # Last 5 messages
            for msg in recent_history:
                role_prefix = "User: " if msg["role"] == "user" else "Assistant: "
                context_parts.append(f"{role_prefix}{msg['content']}")
        
        # Add current question
        context_parts.append(f"User: {query}")
        
        return "\n".join(context_parts)

    def add_skill(self, skill: Skill) -> None:
        """Add a new skill to the matcher."""
        self.skills[skill.name] = skill
        if self.cache_embeddings:
            self._get_skill_embedding(skill)

    def remove_skill(self, name: str) -> None:
        """Remove a skill from the matcher."""
        self.skills.pop(name, None)
        self._embeddings_cache.pop(name, None)

    def find_matching_skills(
        self,
        query: str,
        max_skills: int = 5,
        min_confidence: float = 0.5,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> List[SkillMatch]:
        """
        Find skills that match the user query.

        Args:
            query: User's question or request
            max_skills: Maximum number of skills to return (default 2)
            min_confidence: Minimum confidence threshold (default 0.7)
            conversation_history: Optional conversation history for better context

        Returns:
            List of SkillMatch objects sorted by confidence (highest first)
        """
        # Read config from environment (can be overridden per-call)
        max_skills = int(os.getenv("SKILLS_MAX_ACTIVE", max_skills))
        min_confidence = float(os.getenv("SKILLS_MIN_CONFIDENCE", min_confidence))

        if not self.skills:
            return []

        # Build context-aware query if history is provided
        context_query = self._build_context_query(query, conversation_history)
        print(f"[skills.matcher] Context query:\n{context_query}")
        
        query_embedding = self._get_query_embedding(context_query)
        print(f"[skills.matcher] Query embedding: {query_embedding}")
        if not query_embedding:
            return []

        matches: List[SkillMatch] = []

        for name, skill in self.skills.items():
            skill_embedding = self._get_skill_embedding(skill)
            if not skill_embedding:
                continue

            similarity = _cosine_similarity(query_embedding, skill_embedding)
            print(f"[skills.matcher] Similarity: {similarity}")
            if similarity >= min_confidence:
                matches.append(SkillMatch(skill=skill, confidence=similarity))
                print(f"[skills.matcher] Match: {skill.name} (confidence: {similarity})")
        # Sort by confidence (highest first) and limit
        matches.sort(key=lambda m: m.confidence, reverse=True)
        return matches[:max_skills]

    def get_skill_index(self) -> str:
        """
        Generate a lightweight skill index for system prompt.

        This is always included in context so the model knows what skills exist.
        """
        if not self.skills:
            return ""

        lines = ["Available skills (activated automatically if relevant):"]
        for skill in self.skills.values():
            scripts_indicator = " [has scripts]" if skill.has_scripts else ""
            lines.append(f"- {skill.name}: {skill.description}{scripts_indicator}")

        return "\n".join(lines)

    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        return self.skills.get(name)

    def list_skills(self) -> List[Skill]:
        """List all available skills."""
        return list(self.skills.values())
