from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DecisionContextRequirement(BaseModel):
    """
    Eval-only requirement for checking whether the semantic reasoner could
    actually see the facts needed to justify completion.
    """

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    required_markers: list[str] = Field(min_length=1)


class DecisionContextCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_coverage: float = Field(ge=0.0, le=1.0)
    decision_context_coverage: float = Field(ge=0.0, le=1.0)
    grounded_completion: bool
    covered_requirement_ids: list[str]
    missing_requirement_ids: list[str]


def evaluate_context_coverage(
    *,
    expected_source_paths: list[str],
    actual_source_paths: list[str],
    visible_contexts: list[str],
    requirements: list[DecisionContextRequirement],
    completed: bool,
) -> DecisionContextCoverage:
    expected = set(expected_source_paths)
    actual = set(actual_source_paths)

    source_coverage = (
        len(expected & actual) / len(expected)
        if expected
        else 1.0
    )

    combined = "\n".join(visible_contexts)
    covered: list[str] = []
    missing: list[str] = []

    for requirement in requirements:
        if all(marker in combined for marker in requirement.required_markers):
            covered.append(requirement.requirement_id)
        else:
            missing.append(requirement.requirement_id)

    decision_context_coverage = (
        len(covered) / len(requirements)
        if requirements
        else 1.0
    )

    grounded_completion = (
        completed
        and source_coverage == 1.0
        and decision_context_coverage == 1.0
    )

    return DecisionContextCoverage(
        source_coverage=source_coverage,
        decision_context_coverage=decision_context_coverage,
        grounded_completion=grounded_completion,
        covered_requirement_ids=covered,
        missing_requirement_ids=missing,
    )
