from __future__ import annotations

import pytest

from app.harness.evidence_pack import EvidencePack
from app.repository.code_evidence import CodeEvidence
from app.research.light_reasoner import LightHybridReasoner
from app.research.unified_agent import (
    UnifiedDecisionKind,
    UnifiedResearchDecision,
)


class Delegate:
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, state):
        self.calls += 1
        return UnifiedResearchDecision(
            kind=UnifiedDecisionKind.COMPLETE,
            reason="delegate",
            unresolved_questions=[],
        )


@pytest.mark.asyncio
async def test_light_hybrid_uses_symbol_fast_path_before_llm() -> None:
    delegate = Delegate()
    reasoner = LightHybridReasoner(delegate=delegate)

    decision = await reasoner.decide(
        {
            "query": "How does ToolRuntime enforce timeout handling?",
            "normalized_task": (
                "How does ToolRuntime enforce timeout handling?"
            ),
        }
    )

    assert decision.kind is UnifiedDecisionKind.ACT
    assert decision.action is not None
    assert decision.action.arguments["query"] == "ToolRuntime"
    assert decision.action.arguments["search_mode"] == "symbol"
    assert delegate.calls == 0


@pytest.mark.asyncio
async def test_light_hybrid_delegates_after_evidence_exists() -> None:
    delegate = Delegate()
    reasoner = LightHybridReasoner(delegate=delegate)

    pack = EvidencePack(
        query="q",
        task_intent="t",
        evidence=[
            CodeEvidence(
                repository="TechPilot",
                file_path="app/harness/tool_runtime.py",
                symbol="ToolRuntime",
                line_start=58,
                line_end=263,
                snippet="asyncio.wait_for(... tool.timeout_seconds ...)",
            )
        ],
        provenance_integrity=True,
        incomplete=False,
    )

    decision = await reasoner.decide(
        {
            "query": "How does ToolRuntime enforce timeout handling?",
            "normalized_task": (
                "How does ToolRuntime enforce timeout handling?"
            ),
            "evidence_pack": pack,
        }
    )

    assert decision.kind is UnifiedDecisionKind.COMPLETE
    assert delegate.calls == 1


@pytest.mark.asyncio
async def test_light_hybrid_delegates_ambiguous_multi_symbol_task() -> None:
    delegate = Delegate()
    reasoner = LightHybridReasoner(delegate=delegate)

    decision = await reasoner.decide(
        {
            "query": "Compare RepoExplorer and ToolRuntime.",
            "normalized_task": "Compare RepoExplorer and ToolRuntime.",
        }
    )

    assert decision.kind is UnifiedDecisionKind.COMPLETE
    assert delegate.calls == 1
