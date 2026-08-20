from __future__ import annotations

import pytest

from app.research.contracts import ResearchAction
from app.research.unified_agent import (
    UnifiedDecisionKind,
    UnifiedResearchReasoner,
)


def _repo_action(query: str) -> dict:
    return {
        "tool_name": "repo_explore",
        "arguments": {
            "query": query,
            "task_intent": "inspect repository",
            "search_mode": "symbol",
            "limit": 5,
        },
        "reason": f"find {query}",
    }


class RepairingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {
                "kind": "act",
                "reason": "repeat previous query",
                "unresolved_questions": ["ToolRegistry is still missing"],
                "action": _repo_action("RepoExplorer"),
            }

        return {
            "kind": "act",
            "reason": "cover the uncovered sibling mechanism",
            "unresolved_questions": [],
            "action": _repo_action("ToolRegistry"),
        }


@pytest.mark.asyncio
async def test_duplicate_repo_explore_is_repaired_without_act_execution() -> None:
    provider = RepairingProvider()
    reasoner = UnifiedResearchReasoner(
        provider=provider,
        capabilities={"repo_explore": "read-only repository research"},
        max_repairs=1,
    )

    previous = ResearchAction.model_validate(
        _repo_action("RepoExplorer")
    )

    decision = await reasoner.decide(
        {
            "query": "Explain RepoExplorer and ToolRegistry.",
            "normalized_task": "Explain RepoExplorer and ToolRegistry.",
            "last_action": previous,
            "retry_count": 0,
            "step_count": 4,
            "max_steps": 5,
        }
    )

    assert provider.calls == 2
    assert decision.kind is UnifiedDecisionKind.ACT
    assert decision.action is not None
    assert decision.action.arguments["query"] == "ToolRegistry"


class RetryProvider:
    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict:
        return {
            "kind": "act",
            "reason": "retry transient composite timeout",
            "unresolved_questions": ["same source is still missing"],
            "action": _repo_action("RepoExplorer"),
        }


@pytest.mark.asyncio
async def test_duplicate_repo_explore_is_allowed_for_retryable_failure() -> None:
    reasoner = UnifiedResearchReasoner(
        provider=RetryProvider(),
        capabilities={"repo_explore": "read-only repository research"},
        max_repairs=0,
    )

    previous = ResearchAction.model_validate(
        _repo_action("RepoExplorer")
    )

    decision = await reasoner.decide(
        {
            "query": "Explain RepoExplorer.",
            "normalized_task": "Explain RepoExplorer.",
            "last_action": previous,
            "retry_count": 1,
            "step_count": 2,
            "max_steps": 5,
        }
    )

    assert decision.kind is UnifiedDecisionKind.ACT
    assert decision.action == previous
