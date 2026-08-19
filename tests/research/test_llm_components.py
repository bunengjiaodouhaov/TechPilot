from __future__ import annotations

from typing import Any

import pytest

from app.research.contracts import (
    ResearchState,
    ResearchStep,
    VerificationResult,
)
from app.research.llm_components import (
    LLMResearchActionSelector,
    LLMResearchPlanner,
    ResearchDecisionValidationError,
)


class QueueProvider:
    def __init__(self, *payloads: dict[str, Any]) -> None:
        self._payloads = list(payloads)
        self.calls: list[dict[str, str]] = []

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )
        return self._payloads.pop(0)


@pytest.mark.asyncio
async def test_llm_planner_returns_business_objective_not_tool_call() -> None:
    provider = QueueProvider(
        {
            "steps": [
                {
                    "objective": (
                        "Establish how ToolRuntime validates and bounds execution."
                    ),
                    "source_requirement": (
                        "Authoritative repository implementation."
                    ),
                }
            ]
        }
    )
    planner = LLMResearchPlanner(provider=provider)

    plan = await planner.plan(
        "How does ToolRuntime enforce the execution boundary?"
    )

    assert len(plan) == 1
    assert isinstance(plan[0], ResearchStep)
    assert not hasattr(plan[0], "tool_name")
    assert "Do not choose tools" in provider.calls[0]["system_prompt"]


@pytest.mark.asyncio
async def test_llm_planner_repairs_two_steps_into_one() -> None:
    provider = QueueProvider(
        {
            "steps": [
                {
                    "objective": "Establish input and output validation.",
                    "source_requirement": "Repository source.",
                },
                {
                    "objective": "Establish timeout handling.",
                    "source_requirement": "Repository source.",
                },
            ]
        },
        {
            "steps": [
                {
                    "objective": (
                        "Establish ToolRuntime input/output validation and "
                        "timeout handling."
                    ),
                    "source_requirement": "Authoritative repository source.",
                }
            ]
        },
    )
    planner = LLMResearchPlanner(provider=provider, max_repairs=1)

    plan = await planner.plan(
        "How does ToolRuntime validate I/O and enforce timeouts?"
    )

    assert len(plan) == 1
    assert "timeout" in plan[0].objective
    assert len(provider.calls) == 2
    assert "MERGE" in provider.calls[1]["user_prompt"]


@pytest.mark.asyncio
async def test_llm_planner_fails_after_bounded_repair() -> None:
    invalid = {
        "steps": [
            {"objective": "a"},
            {"objective": "b"},
        ]
    }
    provider = QueueProvider(invalid, invalid)
    planner = LLMResearchPlanner(provider=provider, max_repairs=1)

    with pytest.raises(ResearchDecisionValidationError):
        await planner.plan("research something")

    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_llm_action_selector_uses_verifier_gap_and_allowed_capability() -> None:
    provider = QueueProvider(
        {
            "action": {
                "tool_name": "repo_explore",
                "arguments": {
                    "query": "ToolRuntime",
                    "task_intent": "Establish runtime execution boundaries.",
                    "search_mode": "symbol",
                    "limit": 5,
                },
                "reason": (
                    "The verifier still needs authoritative implementation evidence."
                ),
            }
        }
    )
    selector = LLMResearchActionSelector(
        provider=provider,
        capabilities={
            "repo_explore": (
                "Search repository candidates and materialize authoritative "
                "CodeEvidence through read_file."
            )
        },
    )
    state: ResearchState = {
        "query": "How does ToolRuntime work?",
        "normalized_task": "How does ToolRuntime work?",
        "plan": [
            ResearchStep(
                objective="Establish runtime execution boundaries.",
                source_requirement="Authoritative repository implementation.",
            )
        ],
        "current_step": 0,
        "step_count": 1,
        "max_steps": 4,
        "retry_count": 0,
        "max_retries": 1,
        "verification": VerificationResult(
            sufficient=False,
            reason="No authoritative evidence is available yet.",
            unresolved_questions=[
                "Need the implementation of ToolRuntime.invoke."
            ],
        ),
    }

    action = await selector.select_action(state)

    assert action is not None
    assert action.tool_name == "repo_explore"
    assert action.arguments["query"] == "ToolRuntime"
    user_prompt = provider.calls[0]["user_prompt"]
    assert "Need the implementation of ToolRuntime.invoke." in user_prompt
    assert "repo_explore" in user_prompt


@pytest.mark.asyncio
async def test_llm_action_selector_repairs_unallowed_capability_before_execution() -> None:
    provider = QueueProvider(
        {
            "action": {
                "tool_name": "delete_repository",
                "arguments": {},
                "reason": "bad choice",
            }
        },
        {
            "action": {
                "tool_name": "repo_explore",
                "arguments": {
                    "query": "ToolRuntime",
                    "task_intent": "Research runtime behavior.",
                    "search_mode": "symbol",
                    "limit": 5,
                },
                "reason": "Use the only allowed read-only research capability.",
            }
        },
    )
    selector = LLMResearchActionSelector(
        provider=provider,
        capabilities={"repo_explore": "Read-only repository research."},
        max_repairs=1,
    )

    action = await selector.select_action(
        {
            "query": "q",
            "normalized_task": "q",
            "plan": [ResearchStep(objective="research q")],
            "current_step": 0,
        }
    )

    assert action is not None
    assert action.tool_name == "repo_explore"
    assert len(provider.calls) == 2
    assert "has NOT been executed" in provider.calls[1]["user_prompt"]


@pytest.mark.asyncio
async def test_llm_action_selector_fails_if_repair_stays_unallowed() -> None:
    invalid = {
        "action": {
            "tool_name": "delete_repository",
            "arguments": {},
            "reason": "bad choice",
        }
    }
    provider = QueueProvider(invalid, invalid)
    selector = LLMResearchActionSelector(
        provider=provider,
        capabilities={"repo_explore": "Read-only repository research."},
        max_repairs=1,
    )

    with pytest.raises(ResearchDecisionValidationError):
        await selector.select_action(
            {
                "query": "q",
                "normalized_task": "q",
                "plan": [ResearchStep(objective="research q")],
                "current_step": 0,
            }
        )

    assert len(provider.calls) == 2
