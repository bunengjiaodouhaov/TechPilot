from __future__ import annotations

import json
from typing import Any

import pytest

from app.research.contracts import ResearchAction
from app.research.unified_agent import (
    UnifiedDecisionKind,
    UnifiedResearchDecision,
    UnifiedResearchReasoner,
    build_unified_research_graph,
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
async def test_action_history_is_visible_to_reasoner() -> None:
    provider = CapturingProvider()
    reasoner = UnifiedResearchReasoner(
        provider=provider,
        capabilities={"repo_explore": "read-only repository research"},
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
            "step_count": 3,
            "max_steps": 5,
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


class SequenceReasoner:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, state: dict) -> UnifiedResearchDecision:
        self.calls += 1
        if self.calls == 1:
            return UnifiedResearchDecision(
                kind=UnifiedDecisionKind.ACT,
                reason="first",
                unresolved_questions=["second missing"],
                action=_action("ToolRuntime"),
            )
        if self.calls == 2:
            assert [
                item.arguments["query"]
                for item in state["action_history"]
            ] == ["ToolRuntime"]
            return UnifiedResearchDecision(
                kind=UnifiedDecisionKind.ACT,
                reason="second",
                unresolved_questions=[],
                action=_action("ToolRegistry"),
            )
        return UnifiedResearchDecision(
            kind=UnifiedDecisionKind.NO_ACTIONABLE_PATH,
            reason=(
                "Synthetic control-path fixture stops after action-history "
                "behavior is exercised; it intentionally does not claim completion."
            ),
            unresolved_questions=[
                "Synthetic control fixture intentionally leaves a non-business "
                "obligation unresolved so NO_ACTIONABLE_PATH remains contract-valid."
            ],
        )


class FakeExecutor:
    async def execute(
        self,
        *,
        action: ResearchAction,
        state: dict,
        trace_metadata: dict[str, Any],
    ) -> dict:
        return {
            "last_tool_result": None,
            "step_count": state.get("step_count", 0) + 1,
            "retry_count": 0,
        }


class Finalizer:
    def finalize(self, state: dict) -> str:
        return state["termination_reason"].value


@pytest.mark.asyncio
async def test_graph_accumulates_action_history_across_acts() -> None:
    graph = build_unified_research_graph(
        executor=FakeExecutor(),
        reasoner=SequenceReasoner(),
        finalizer=Finalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": "q",
            "max_steps": 4,
        }
    )

    assert [
        item.arguments["query"]
        for item in result["action_history"]
    ] == [
        "ToolRuntime",
        "ToolRegistry",
    ]
