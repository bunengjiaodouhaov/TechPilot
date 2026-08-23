from __future__ import annotations

import re

from .schemas import JobSearchSpec


class QueryParser:
    """Small deterministic baseline for job-search intent normalization.

    It is intentionally conservative. Unknown fields stay unset rather than
    being guessed.
    """

    _LOCATION_ALIASES = {
        "上海": "Shanghai",
        "shanghai": "Shanghai",
        "北京": "Beijing",
        "beijing": "Beijing",
        "深圳": "Shenzhen",
        "shenzhen": "Shenzhen",
        "广州": "Guangzhou",
        "guangzhou": "Guangzhou",
        "东京": "Tokyo",
        "tokyo": "Tokyo",
    }

    _ROLE_PATTERNS = (
        "AI Engineer",
        "LLM Engineer",
        "Backend Engineer",
        "RAG Engineer",
        "Machine Learning Engineer",
    )

    _DOMAINS = ("LLM", "RAG", "Agent", "Python", "Backend", "AI")

    def parse(self, query: str) -> JobSearchSpec:
        normalized = query.strip()
        if not normalized:
            raise ValueError("query must not be empty")

        return JobSearchSpec(
            query=normalized,
            role=self._extract_role(normalized),
            location=self._extract_location(normalized),
            domains=self._extract_domains(normalized),
            salary_min=self._extract_salary_min(normalized),
        )

    def _extract_location(self, text: str) -> str | None:
        folded = text.casefold()
        for alias, canonical in self._LOCATION_ALIASES.items():
            if alias.casefold() in folded:
                return canonical
        return None

    def _extract_role(self, text: str) -> str | None:
        folded = text.casefold()
        for role in self._ROLE_PATTERNS:
            if role.casefold() in folded:
                return role
        return None

    def _extract_domains(self, text: str) -> list[str]:
        folded = text.casefold()
        return [item for item in self._DOMAINS if item.casefold() in folded]

    @staticmethod
    def _extract_salary_min(text: str) -> int | None:
        match = re.search(r"(?<!\d)(\d{1,3})\s*[kK](?:\+|以上)?", text)
        if not match:
            return None
        return int(match.group(1)) * 1000
