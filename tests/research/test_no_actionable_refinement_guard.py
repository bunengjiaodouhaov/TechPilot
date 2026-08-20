from __future__ import annotations

import pytest

from app.harness.evidence_pack import EvidencePack
from app.repository.code_evidence import CodeEvidence
from app.research.contracts import ResearchAction
from app.research.unified_agent import (
    UnifiedDecisionKind,
    UnifiedResearchDecision,
    UnifiedResearchReasoner,
)


class NeverProvider:
    async def generate_json(self, *, system_prompt: str, user_prompt: str):
        raise AssertionError("provider should not be called")


def evidence_pack() -> EvidencePack:
    return EvidencePack(
        query="trace provider failure control",
        task_intent="trace provider failure control",
        evidence=[
            CodeEvidence(
                repository="TechPilot",
                file_path="app/research/decision_llm.py",
                symbol="ResearchDecisionProviderError",
                line_start=11,
                line_end=25,
                snippet="class ResearchDecisionProviderError(RuntimeError): ...",
            )
        ],
        provenance_integrity=True,
        incomplete=False,
        issues=[],
    )


def no_actionable() -> UnifiedResearchDecision:
    return UnifiedResearchDecision(
        kind=UnifiedDecisionKind.NO_ACTIONABLE_PATH,
        reason="Classification implementation is still unresolved.",
        unresolved_questions=[
            "Where are provider failures classified in production code?"
        ],
        action=None,
    )


def reasoner() -> UnifiedResearchReasoner:
    return UnifiedResearchReasoner(
        provider=NeverProvider(),
        capabilities={
            "repo_explore": "repository research with path, symbol, code, hybrid"
        },
    )


def test_no_actionable_rejected_before_known_source_refinement() -> None:
    with pytest.raises(
        ValueError,
        match="known-source exact-path refinement",
    ):
        reasoner()._validate_decision(
            no_actionable(),
            {
                "query": "trace provider failure control",
                "evidence_pack": evidence_pack(),
                "step_count": 3,
                "max_steps": 5,
                "action_history": [],
            },
        )


def test_no_actionable_allowed_after_exact_path_refinement() -> None:
    reasoner()._validate_decision(
        no_actionable(),
        {
            "query": "trace provider failure control",
            "evidence_pack": evidence_pack(),
            "step_count": 4,
            "max_steps": 5,
            "action_history": [
                ResearchAction(
                    tool_name="repo_explore",
                    arguments={
                        "query": "app/research/decision_llm.py",
                        "task_intent": "inspect provider classification",
                        "search_mode": "path",
                        "limit": 1,
                    },
                    reason="refine known source",
                )
            ],
        },
    )


def test_no_actionable_allowed_when_act_budget_is_exhausted() -> None:
    reasoner()._validate_decision(
        no_actionable(),
        {
            "query": "trace provider failure control",
            "evidence_pack": evidence_pack(),
            "step_count": 5,
            "max_steps": 5,
            "action_history": [],
        },
    )
