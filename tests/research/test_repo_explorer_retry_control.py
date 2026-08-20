from __future__ import annotations

import pytest

from app.harness.evidence_pack import (
    EvidenceIssueKind,
    EvidencePack,
    EvidencePackIssue,
)
from app.harness.tool_runtime import ToolErrorCode
from app.repository.code_evidence import CodeEvidence
from app.research.contracts import ResearchAction, TerminationReason
from app.research.execution import RepoExplorerActionExecutor
from app.research.unified_agent import (
    UnifiedDecisionKind,
    UnifiedResearchDecision,
    build_unified_research_graph,
)


def _pack(
    *,
    path: str | None = None,
    timeout: bool = False,
) -> EvidencePack:
    evidence = []
    if path is not None:
        evidence.append(
            CodeEvidence(
                repository="TechPilot",
                file_path=path,
                symbol="Example",
                line_start=1,
                line_end=1,
                snippet="class Example: pass",
            )
        )

    issues = []
    if timeout:
        issues.append(
            EvidencePackIssue(
                kind=EvidenceIssueKind.TOOL_FAILURE,
                tool_name="read_file",
                error_code=ToolErrorCode.TIMEOUT,
            )
        )

    return EvidencePack(
        query="q",
        task_intent="t",
        evidence=evidence,
        provenance_integrity=True,
        incomplete=bool(issues),
        issues=issues,
    )


class FakeExplorer:
    def __init__(self, packs: list[EvidencePack]) -> None:
        self._packs = list(packs)
        self.calls = 0

    async def explore(self, request, *, trace_metadata=None) -> EvidencePack:
        self.calls += 1
        if not self._packs:
            raise AssertionError("unexpected extra explore call")
        return self._packs.pop(0)


def _action(query: str = "Example") -> ResearchAction:
    return ResearchAction(
        tool_name="repo_explore",
        arguments={
            "query": query,
            "task_intent": "inspect repository",
            "search_mode": "symbol",
            "limit": 3,
        },
        reason="inspect repository",
    )


@pytest.mark.asyncio
async def test_composite_timeout_increments_retry_and_success_resets_it() -> None:
    explorer = FakeExplorer(
        [
            _pack(timeout=True),
            _pack(path="app/example.py"),
        ]
    )
    executor = RepoExplorerActionExecutor(explorer=explorer)

    first = await executor.execute(
        action=_action(),
        state={"query": "q", "step_count": 1, "retry_count": 0},
        trace_metadata={},
    )
    assert first["step_count"] == 2
    assert first["retry_count"] == 1
    assert first["evidence_pack"].issues[0].error_code is ToolErrorCode.TIMEOUT

    second = await executor.execute(
        action=_action(),
        state={
            "query": "q",
            "step_count": first["step_count"],
            "retry_count": first["retry_count"],
        },
        trace_metadata={},
    )
    assert second["step_count"] == 3
    assert second["retry_count"] == 0
    assert second["evidence_pack"].issues == []


class NeedTwoSourcesReasoner:
    def decide(self, state: dict) -> UnifiedResearchDecision:
        pack = state.get("evidence_pack")
        paths = {
            item.file_path
            for item in (pack.evidence if pack is not None else [])
        }

        if "app/repo.py" not in paths:
            query = "repo"
        elif "app/runtime.py" not in paths:
            query = "runtime"
        else:
            return UnifiedResearchDecision(
                kind=UnifiedDecisionKind.COMPLETE,
                reason="both sources present",
                unresolved_questions=[],
            )

        return UnifiedResearchDecision(
            kind=UnifiedDecisionKind.ACT,
            reason=f"need {query}",
            unresolved_questions=[f"{query} missing"],
            action=_action(query),
        )


class Finalizer:
    def finalize(self, state: dict) -> str:
        return state["termination_reason"].value


@pytest.mark.asyncio
async def test_persistent_composite_timeout_stops_at_retry_budget() -> None:
    explorer = FakeExplorer(
        [
            _pack(path="app/repo.py"),
            _pack(timeout=True),
            _pack(timeout=True),
        ]
    )
    graph = build_unified_research_graph(
        executor=RepoExplorerActionExecutor(explorer=explorer),
        reasoner=NeedTwoSourcesReasoner(),
        finalizer=Finalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": "explain repo and runtime",
            "max_steps": 5,
            "max_retries": 1,
            "max_decision_retries": 1,
        }
    )

    assert result["termination_reason"] is TerminationReason.RETRY_EXHAUSTED
    assert result["step_count"] == 3
    assert result["retry_count"] == 2
    assert explorer.calls == 3

    paths = {
        item.file_path
        for item in result["evidence_pack"].evidence
    }
    assert "app/repo.py" in paths
    assert "app/runtime.py" not in paths
