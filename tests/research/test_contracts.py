import pytest
from pydantic import ValidationError

from app.research.contracts import (
    ResearchStep,
    TerminationReason,
    VerificationResult,
)


def test_research_step_is_business_objective_not_tool_call() -> None:
    step = ResearchStep(
        objective="Confirm the checkpoint implementation mechanism",
        source_requirement="authoritative implementation",
    )

    assert step.objective == "Confirm the checkpoint implementation mechanism"
    assert step.source_requirement == "authoritative implementation"
    assert not hasattr(step, "tool_name")


def test_research_step_rejects_blank_objective() -> None:
    with pytest.raises(ValidationError):
        ResearchStep(objective="   ")


def test_verification_result_keeps_control_decision_outside_verifier() -> None:
    result = VerificationResult(
        sufficient=False,
        reason="Only a retrieval candidate exists; authoritative content is missing.",
        unresolved_questions=["What does the implementation actually define?"],
    )

    assert result.sufficient is False
    assert not hasattr(result, "can_continue")


def test_termination_reason_is_structured() -> None:
    assert TerminationReason.MAX_STEPS.value == "max_steps"
    assert TerminationReason.PERMANENT_FAILURE.value == "permanent_failure"
