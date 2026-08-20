from __future__ import annotations

import json

import pytest

from app.harness.evidence_pack import EvidencePack
from app.repository.code_evidence import CodeEvidence
from app.research.contracts import (
    ActionExecutionOutcome,
    ResearchAction,
)
from app.research.unified_agent import (
    AccumulatingActionExecutor,
    UnifiedResearchReasoner,
)


class CompositeInner:
    async def execute(self, *, action, state, trace_metadata):
        pack = EvidencePack(
            query=state["query"],
            task_intent=state["query"],
            evidence=[
                CodeEvidence(
                    repository="TechPilot",
                    file_path="app/repository/repo_explorer.py",
                    symbol="RepoExplorer",
                    line_start=1,
                    line_end=1,
                    snippet="class RepoExplorer:",
                )
            ],
            provenance_integrity=True,
            incomplete=False,
            issues=[],
        )
        return {
            "last_tool_result": None,
            "evidence_pack": pack,
            "step_count": state.get("step_count", 0) + 1,
            "retry_count": 0,
        }


class Provider:
    async def generate_json(self, *, system_prompt, user_prompt):
        return {
            "kind": "complete",
            "reason": "evidence is sufficient",
            "unresolved_questions": [],
            "action": None,
        }


@pytest.mark.asyncio
async def test_composite_action_outcome_does_not_depend_on_tool_result() -> None:
    executor = AccumulatingActionExecutor(CompositeInner())
    action = ResearchAction(
        tool_name="repo_explore",
        arguments={"query": "RepoExplorer"},
        reason="find repository evidence",
    )

    updates = await executor.execute(
        action=action,
        state={"query": "How does RepoExplorer work?", "step_count": 0},
        trace_metadata={},
    )

    outcome = updates["last_action_outcome"]
    assert isinstance(outcome, ActionExecutionOutcome)
    assert outcome.capability == "repo_explore"
    assert outcome.tool_result_present is False
    assert outcome.tool_result_ok is None
    assert outcome.evidence_returned_count == 1
    assert outcome.new_evidence_count == 1
    assert outcome.issue_count == 0


def test_reasoner_prompt_surfaces_composite_action_outcome() -> None:
    reasoner = UnifiedResearchReasoner(
        provider=Provider(),
        capabilities={"repo_explore": "read-only repository research"},
    )
    outcome = ActionExecutionOutcome(
        capability="repo_explore",
        tool_result_present=False,
        tool_result_ok=None,
        evidence_returned_count=3,
        new_evidence_count=2,
        issue_count=0,
        retry_count_after=0,
        termination_reason=None,
    )

    prompt = reasoner._build_user_prompt(
        {
            "query": "trace repository behavior",
            "last_tool_result": None,
            "last_action_outcome": outcome,
        }
    )

    assert '"last_tool_result": null' in prompt
    assert '"last_action_outcome"' in prompt
    assert '"evidence_returned_count": 3' in prompt
    assert '"new_evidence_count": 2' in prompt
