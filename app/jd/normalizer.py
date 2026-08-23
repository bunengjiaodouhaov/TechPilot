from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NormalizedSkill:
    original: str
    canonical_name: str


class SkillNormalizer:
    """Conservative alias normalization; unknown skills pass through unchanged."""

    DEFAULT_ALIASES = {
        "retrieval augmented generation": "RAG",
        "retrieval-augmented generation": "RAG",
        "rag": "RAG",
        "large language model": "LLM",
        "large language models": "LLM",
        "llm": "LLM",
        "postgres": "PostgreSQL",
        "postgresql": "PostgreSQL",
        "fastapi": "FastAPI",
        "python": "Python",
        "qdrant": "Qdrant",
    }

    def __init__(self, aliases: dict[str, str] | None = None) -> None:
        self._aliases = {
            key.casefold().strip(): value
            for key, value in (aliases or self.DEFAULT_ALIASES).items()
        }

    def normalize(self, skill: str) -> NormalizedSkill:
        original = skill.strip()
        if not original:
            raise ValueError("skill must not be empty")
        canonical = self._aliases.get(original.casefold(), original)
        return NormalizedSkill(original=original, canonical_name=canonical)
