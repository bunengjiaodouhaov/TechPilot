from __future__ import annotations

import re
from typing import Awaitable, Protocol

from app.research.contracts import ResearchAction
from app.research.unified_agent import (
    UnifiedAgentState,
    UnifiedDecisionKind,
    UnifiedResearchDecision,
)


class UnifiedReasonerLike(Protocol):
    def decide(
        self,
        state: UnifiedAgentState,
    ) -> UnifiedResearchDecision | Awaitable[UnifiedResearchDecision]:
        ...


class LightHybridReasoner:
    """
    Cheap/narrow agent policy.

    When the task exposes one clear CamelCase repository symbol and no
    authoritative evidence exists yet, take the obvious deterministic
    symbol-first action. Later semantic sufficiency / gap decisions are still
    delegated to the configured LLM reasoner.

    This is intentionally narrower than Research Agent behavior.
    """

    _camel_case_symbol = re.compile(
        r"\b[A-Z][A-Za-z0-9]+(?:[A-Z][A-Za-z0-9]*)*\b"
    )

    _ignored_tokens = frozenset(
        {
            "How",
            "What",
            "Where",
            "Why",
            "When",
            "Using",
            "Explain",
            "Compare",
            "Check",
            "Find",
            "Research",
        }
    )

    def __init__(
        self,
        *,
        delegate: UnifiedReasonerLike,
        capability_name: str = "repo_explore",
        symbol_limit: int = 5,
    ) -> None:
        self._delegate = delegate
        self._capability_name = capability_name
        self._symbol_limit = symbol_limit

    async def decide(
        self,
        state: UnifiedAgentState,
    ) -> UnifiedResearchDecision:
        deterministic = self._deterministic_first_action(state)
        if deterministic is not None:
            return deterministic

        decision = self._delegate.decide(state)
        if hasattr(decision, "__await__"):
            return await decision
        return decision

    def _deterministic_first_action(
        self,
        state: UnifiedAgentState,
    ) -> UnifiedResearchDecision | None:
        pack = state.get("evidence_pack")
        if pack is not None and pack.evidence:
            return None

        if state.get("last_action") is not None:
            return None

        query = state.get("normalized_task", state["query"])
        symbols = self._extract_symbols(query)

        if len(symbols) != 1:
            return None

        symbol = symbols[0]
        return UnifiedResearchDecision(
            kind=UnifiedDecisionKind.ACT,
            reason=(
                "The task names one explicit repository symbol and no "
                "authoritative evidence exists yet, so the lowest-cost "
                "high-precision first action is deterministic symbol search."
            ),
            unresolved_questions=[
                f"Need authoritative implementation evidence for {symbol}."
            ],
            action=ResearchAction(
                tool_name=self._capability_name,
                arguments={
                    "query": symbol,
                    "task_intent": query,
                    "search_mode": "symbol",
                    "limit": self._symbol_limit,
                },
                reason=(
                    f"Materialize the {symbol} definition before spending an "
                    "LLM decision on broader retrieval."
                ),
            ),
        )

    def _extract_symbols(self, query: str) -> list[str]:
        values = []
        for value in self._camel_case_symbol.findall(query):
            if value in self._ignored_tokens:
                continue
            if value not in values:
                values.append(value)
        return values
