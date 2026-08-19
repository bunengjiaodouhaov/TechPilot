from __future__ import annotations

from app.harness.agent_event import AgentEvent, AgentEventType
from app.harness.evidence_pack import EvidencePack
from app.repository.code_evidence import CodeEvidence
from app.research.contracts import TerminationReason
from app.research.evaluation import (
    EvidenceRequirement,
    ResearchGoldenCase,
    evaluate_research_run,
)


def _case() -> ResearchGoldenCase:
    return ResearchGoldenCase(
        case_id="case-1",
        query="How is the boundary enforced?",
        repo_query="ToolRuntime",
        search_mode="symbol",
        expected_action="repo_explore",
        expected_termination=TerminationReason.COMPLETED,
        max_steps=2,
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="permission",
                file_path="app/harness/tool_runtime.py",
                required_terms=["PERMISSION_DENIED", "_allowed_risk_levels"],
            ),
            EvidenceRequirement(
                requirement_id="timeout",
                file_path="app/harness/tool_runtime.py",
                required_terms=["asyncio.wait_for", "TIMEOUT"],
            ),
        ],
    )


def _event(action: str) -> AgentEvent:
    return AgentEvent(
        trace_id="trace",
        event_type=AgentEventType.TOOL_CALL,
        component="tool_runtime",
        tool_name="search_symbol",
        trace_metadata={"research_action": action},
    )


def test_evaluation_reports_full_coverage_and_success() -> None:
    pack = EvidencePack(
        query="q",
        task_intent="t",
        evidence=[
            CodeEvidence(
                repository="TechPilot",
                file_path="app/harness/tool_runtime.py",
                symbol="ToolRuntime",
                line_start=1,
                line_end=2,
                snippet=(
                    "_allowed_risk_levels PERMISSION_DENIED "
                    "asyncio.wait_for TIMEOUT"
                ),
            )
        ],
        provenance_integrity=True,
        incomplete=False,
    )

    result = evaluate_research_run(
        case=_case(),
        state={
            "query": "q",
            "termination_reason": TerminationReason.COMPLETED,
            "step_count": 1,
            "evidence_pack": pack,
        },
        events=[_event("repo_explore")],
    )

    assert result.task_success is True
    assert result.evidence_coverage == 1.0
    assert result.provenance_integrity is True
    assert result.tool_selection_correctness is True
    assert result.termination_correctness is True
    assert result.step_count == 1
    assert result.missing_requirement_ids == []


def test_evaluation_exposes_missing_evidence_and_wrong_action() -> None:
    pack = EvidencePack(
        query="q",
        task_intent="t",
        evidence=[
            CodeEvidence(
                repository="TechPilot",
                file_path="app/harness/tool_runtime.py",
                symbol="ToolRuntime",
                line_start=1,
                line_end=1,
                snippet="_allowed_risk_levels PERMISSION_DENIED",
            )
        ],
        provenance_integrity=True,
        incomplete=False,
    )

    result = evaluate_research_run(
        case=_case(),
        state={
            "query": "q",
            "termination_reason": TerminationReason.COMPLETED,
            "step_count": 1,
            "evidence_pack": pack,
        },
        events=[_event("wrong_action")],
    )

    assert result.task_success is False
    assert result.evidence_coverage == 0.5
    assert result.tool_selection_correctness is False
    assert result.covered_requirement_ids == ["permission"]
    assert result.missing_requirement_ids == ["timeout"]
