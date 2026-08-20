from __future__ import annotations

from typing import Any

import pytest

from app.harness.evidence_pack import EvidencePack
from app.repository.code_evidence import CodeEvidence
from app.research.contracts import ResearchAction, TerminationReason
from app.research.unified_agent import (
    AccumulatingActionExecutor,
    UnifiedDecisionKind,
    UnifiedResearchDecision,
    build_unified_research_graph,
)


def _evidence(path: str, symbol: str) -> EvidencePack:
    return EvidencePack(
        query="q",
        task_intent="t",
        evidence=[
            CodeEvidence(
                repository="TechPilot",
                file_path=path,
                symbol=symbol,
                line_start=1,
                line_end=2,
                snippet=f"class {symbol}: pass",
            )
        ],
        provenance_integrity=True,
        incomplete=False,
    )


class FakeExecutor:
    async def execute(
        self,
        *,
        action: ResearchAction,
        state: dict,
        trace_metadata: dict[str, Any],
    ) -> dict:
        path = (
            "app/repository/repo_explorer.py"
            if action.arguments["query"] == "RepoExplorer"
            else "app/harness/tool_runtime.py"
        )
        symbol = action.arguments["query"]
        return {
            "last_tool_result": None,
            "evidence_pack": _evidence(path, symbol),
            "step_count": state.get("step_count", 0) + 1,
            "retry_count": 0,
        }


class GapDrivenReasoner:
    def __init__(self) -> None:
        self.observed_paths: list[set[str]] = []

    def decide(self, state: dict) -> UnifiedResearchDecision:
        pack = state.get("evidence_pack")
        paths = {
            item.file_path
            for item in (pack.evidence if pack is not None else [])
        }
        self.observed_paths.append(paths)

        if "app/repository/repo_explorer.py" not in paths:
            return UnifiedResearchDecision(
                kind=UnifiedDecisionKind.ACT,
                reason="Need RepoExplorer evidence.",
                unresolved_questions=[
                    "How are candidates materialized?"
                ],
                action=ResearchAction(
                    tool_name="repo_explore",
                    arguments={"query": "RepoExplorer"},
                    reason="Get RepoExplorer implementation.",
                ),
            )

        if "app/harness/tool_runtime.py" not in paths:
            return UnifiedResearchDecision(
                kind=UnifiedDecisionKind.ACT,
                reason="RepoExplorer is covered; ToolRuntime is still missing.",
                unresolved_questions=[
                    "How are permission and timeout enforced?"
                ],
                action=ResearchAction(
                    tool_name="repo_explore",
                    arguments={"query": "ToolRuntime"},
                    reason="Get ToolRuntime implementation.",
                ),
            )

        return UnifiedResearchDecision(
            kind=UnifiedDecisionKind.COMPLETE,
            reason="Both mechanisms are directly supported.",
            unresolved_questions=[],
        )


class Finalizer:
    def finalize(self, state: dict) -> str:
        return state["termination_reason"].value


@pytest.mark.asyncio
async def test_unified_loop_changes_action_from_evidence_gap() -> None:
    reasoner = GapDrivenReasoner()
    graph = build_unified_research_graph(
        executor=FakeExecutor(),
        reasoner=reasoner,
        finalizer=Finalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": "Explain RepoExplorer and ToolRuntime.",
            "max_steps": 4,
            "max_retries": 1,
        }
    )

    assert result["termination_reason"] is TerminationReason.COMPLETED
    assert result["step_count"] == 2

    paths = {
        item.file_path
        for item in result["evidence_pack"].evidence
    }
    assert paths == {
        "app/repository/repo_explorer.py",
        "app/harness/tool_runtime.py",
    }
    assert reasoner.observed_paths == [
        set(),
        {"app/repository/repo_explorer.py"},
        {
            "app/repository/repo_explorer.py",
            "app/harness/tool_runtime.py",
        },
    ]


@pytest.mark.asyncio
async def test_unified_loop_allows_final_semantic_decision_at_step_budget() -> None:
    reasoner = GapDrivenReasoner()
    graph = build_unified_research_graph(
        executor=FakeExecutor(),
        reasoner=reasoner,
        finalizer=Finalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": "Explain RepoExplorer and ToolRuntime.",
            "max_steps": 2,
            "max_retries": 1,
        }
    )

    assert result["termination_reason"] is TerminationReason.COMPLETED
    assert result["step_count"] == 2
    assert reasoner.observed_paths[-1] == {
        "app/repository/repo_explorer.py",
        "app/harness/tool_runtime.py",
    }


@pytest.mark.asyncio
async def test_unified_loop_enforces_max_steps_outside_llm() -> None:
    class NeverDone:
        def decide(self, state: dict) -> UnifiedResearchDecision:
            return UnifiedResearchDecision(
                kind=UnifiedDecisionKind.ACT,
                reason="keep going",
                unresolved_questions=["still missing"],
                action=ResearchAction(
                    tool_name="repo_explore",
                    arguments={"query": "RepoExplorer"},
                    reason="again",
                ),
            )

    graph = build_unified_research_graph(
        executor=FakeExecutor(),
        reasoner=NeverDone(),
        finalizer=Finalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": "q",
            "max_steps": 1,
        }
    )

    assert result["termination_reason"] is TerminationReason.MAX_STEPS
    assert result["step_count"] == 1
