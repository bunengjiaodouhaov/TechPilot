from __future__ import annotations

import json

import pytest

from app.research.contracts import (
    ResearchAction,
    VerificationResult,
)
from app.research.execution_policy import (
    ExecutionProfile,
    ProfiledUnifiedResearchReasoner,
)
from app.research.task_router import (
    ExecutionRoute,
    ModelTier,
)


def _action(query: str) -> ResearchAction:
    return ResearchAction(
        tool_name="repo_explore",
        arguments={
            "query": query,
            "task_intent": f"find {query}",
            "search_mode": "symbol",
            "limit": 5,
        },
        reason=f"find {query}",
    )


class CapturingProvider:
    def __init__(self) -> None:
        self.user_prompt: str | None = None

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict:
        self.user_prompt = user_prompt
        return {
            "kind": "no_actionable_path",
            "reason": "capture prompt",
            "unresolved_questions": ["ToolRegistry still missing"],
            "action": None,
        }


@pytest.mark.asyncio
async def test_profiled_reasoner_receives_control_state_parity() -> None:
    provider = CapturingProvider()
    profile = ExecutionProfile(
        route=ExecutionRoute.RESEARCH_AGENT,
        model_tier=ModelTier.LARGE,
        model_name="test-model",
        max_steps=5,
        max_retries=2,
        max_decision_output_tokens=1400,
        max_evidence_items=8,
        evidence_snippet_characters=5000,
    )
    reasoner = ProfiledUnifiedResearchReasoner(
        provider=provider,
        capabilities={"repo_explore": "read-only repository research"},
        profile=profile,
    )

    history = [
        _action("ToolRuntime"),
        _action("RepoExplorer"),
        _action("read_file"),
    ]

    await reasoner.decide(
        {
            "query": "Explain ToolRuntime, RepoExplorer, and ToolRegistry.",
            "normalized_task": (
                "Explain ToolRuntime, RepoExplorer, and ToolRegistry."
            ),
            "action_history": history,
            "last_action": history[-1],
            "verification": VerificationResult(
                sufficient=False,
                reason="ToolRegistry has not been covered.",
                unresolved_questions=[
                    "How does ToolRegistry represent an unavailable tool?"
                ],
            ),
            "step_count": 3,
            "max_steps": 5,
            "retry_count": 0,
            "max_retries": 2,
        }
    )

    assert provider.user_prompt is not None
    payload_text = provider.user_prompt.split(
        "Current agent state JSON:\n",
        1,
    )[1].rsplit("\n\nReturn one decision JSON.", 1)[0]
    payload = json.loads(payload_text)

    assert [
        item["arguments"]["query"]
        for item in payload["action_history"]
    ] == [
        "ToolRuntime",
        "RepoExplorer",
        "read_file",
    ]
    assert payload["previous_verification"] == {
        "sufficient": False,
        "reason": "ToolRegistry has not been covered.",
        "unresolved_questions": [
            "How does ToolRegistry represent an unavailable tool?"
        ],
    }


@pytest.mark.asyncio
async def test_profiled_reasoner_still_exposes_execution_profile() -> None:
    provider = CapturingProvider()
    profile = ExecutionProfile(
        route=ExecutionRoute.RESEARCH_AGENT,
        model_tier=ModelTier.LARGE,
        model_name="test-model",
        max_steps=5,
        max_retries=2,
        max_decision_output_tokens=1400,
        max_evidence_items=8,
        evidence_snippet_characters=5000,
    )
    reasoner = ProfiledUnifiedResearchReasoner(
        provider=provider,
        capabilities={"repo_explore": "read-only repository research"},
        profile=profile,
    )

    await reasoner.decide(
        {
            "query": "q",
            "step_count": 0,
            "max_steps": 5,
        }
    )

    assert provider.user_prompt is not None
    payload_text = provider.user_prompt.split(
        "Current agent state JSON:\n",
        1,
    )[1].rsplit("\n\nReturn one decision JSON.", 1)[0]
    payload = json.loads(payload_text)

    assert payload["execution_profile"]["route"] == "research_agent"
    assert payload["execution_profile"]["max_steps"] == 5
    assert payload["action_history"] == []
    assert payload["previous_verification"] is None
