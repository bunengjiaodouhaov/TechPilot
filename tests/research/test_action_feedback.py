from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRiskLevel, ToolRuntime
from app.research.contracts import (
    ResearchAction,
    ResearchState,
    ResearchStep,
    VerificationResult,
)
from app.research.graph import build_research_graph
from app.research.llm_components import LLMResearchActionSelector
from app.research.repo_workload import (
    EvidenceReportFinalizer,
    SingleObjectivePlanner,
)


class QueueProvider:
    def __init__(self, *payloads: dict[str, Any]) -> None:
        self._payloads = list(payloads)
        self.calls: list[str] = []

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        self.calls.append(user_prompt)
        return self._payloads.pop(0)


@pytest.mark.asyncio
async def test_selector_prompt_contains_previous_action() -> None:
    provider = QueueProvider({"action": None})
    selector = LLMResearchActionSelector(
        provider=provider,
        capabilities={"repo_explore": "Read-only repo research."},
    )

    await selector.select_action(
        {
            "query": "q",
            "normalized_task": "q",
            "plan": [ResearchStep(objective="research q")],
            "current_step": 0,
            "last_action": ResearchAction(
                tool_name="repo_explore",
                arguments={
                    "query": "bad broad query",
                    "search_mode": "symbol",
                },
                reason="first try",
            ),
            "verification": VerificationResult(
                sufficient=False,
                reason="No evidence.",
                unresolved_questions=["Need authoritative evidence."],
            ),
        }
    )

    prompt = provider.calls[0]
    assert '"last_action"' in prompt
    assert "bad broad query" in prompt
    assert "Need authoritative evidence." in prompt


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


class EchoOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


class EchoTool:
    name = "echo"
    description = "echo"
    input_schema = EchoInput
    output_schema = EchoOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 1.0
    max_retries = 0

    async def execute(self, tool_input: EchoInput) -> EchoOutput:
        return EchoOutput(value=tool_input.value)


class OneActionSelector:
    def select_action(self, state: ResearchState) -> ResearchAction | None:
        return ResearchAction(
            tool_name="echo",
            arguments={"value": "x"},
            reason="exercise last_action persistence",
        )


class NeverEnoughVerifier:
    def verify(self, state: ResearchState) -> VerificationResult:
        return VerificationResult(
            sufficient=False,
            reason="not enough",
            unresolved_questions=["more"],
        )


@pytest.mark.asyncio
async def test_act_persists_last_action_in_state() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    graph = build_research_graph(
        registry=registry,
        runtime=ToolRuntime(),
        planner=SingleObjectivePlanner(),
        action_selector=OneActionSelector(),
        verifier=NeverEnoughVerifier(),
        finalizer=EvidenceReportFinalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": "q",
            "max_steps": 1,
        }
    )

    assert result["last_action"].tool_name == "echo"
    assert result["last_action"].arguments == {"value": "x"}
