from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.harness.agent_event import AgentEvent
from app.harness.evidence_pack import EvidencePack
from app.research.contracts import ResearchState, TerminationReason


class EvidenceRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    file_path: str
    required_terms: list[str] = Field(min_length=1)


class ResearchGoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    query: str
    repo_query: str
    search_mode: str
    expected_action: str
    expected_termination: TerminationReason
    max_steps: int = Field(ge=1)
    evidence_requirements: list[EvidenceRequirement] = Field(min_length=1)


class ResearchEvalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    task_success: bool
    evidence_coverage: float = Field(ge=0.0, le=1.0)
    provenance_integrity: bool
    tool_selection_correctness: bool
    termination_correctness: bool
    step_count: int = Field(ge=0)
    covered_requirement_ids: list[str]
    missing_requirement_ids: list[str]


def evaluate_research_run(
    *,
    case: ResearchGoldenCase,
    state: ResearchState,
    events: list[AgentEvent],
) -> ResearchEvalResult:
    pack = state.get("evidence_pack")
    covered, missing = _evaluate_evidence_requirements(
        pack=pack,
        requirements=case.evidence_requirements,
    )
    coverage = len(covered) / len(case.evidence_requirements)

    selected_actions = {
        str(event.trace_metadata.get("research_action"))
        for event in events
        if event.trace_metadata.get("research_action")
    }
    tool_selection_correctness = selected_actions == {case.expected_action}

    actual_termination = state.get("termination_reason")
    termination_correctness = actual_termination == case.expected_termination
    provenance_integrity = bool(
        pack is not None and pack.provenance_integrity
    )

    task_success = bool(
        termination_correctness
        and actual_termination is TerminationReason.COMPLETED
        and coverage == 1.0
        and provenance_integrity
        and tool_selection_correctness
    )

    return ResearchEvalResult(
        case_id=case.case_id,
        task_success=task_success,
        evidence_coverage=coverage,
        provenance_integrity=provenance_integrity,
        tool_selection_correctness=tool_selection_correctness,
        termination_correctness=termination_correctness,
        step_count=state.get("step_count", 0),
        covered_requirement_ids=covered,
        missing_requirement_ids=missing,
    )


def _evaluate_evidence_requirements(
    *,
    pack: EvidencePack | None,
    requirements: list[EvidenceRequirement],
) -> tuple[list[str], list[str]]:
    if pack is None:
        return [], [item.requirement_id for item in requirements]

    covered: list[str] = []
    missing: list[str] = []

    for requirement in requirements:
        matched = any(
            evidence.file_path == requirement.file_path
            and all(
                term in evidence.snippet
                for term in requirement.required_terms
            )
            for evidence in pack.evidence
        )
        if matched:
            covered.append(requirement.requirement_id)
        else:
            missing.append(requirement.requirement_id)

    return covered, missing
