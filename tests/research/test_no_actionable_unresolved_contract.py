import pytest
from pydantic import ValidationError

from app.research.unified_agent import (
    UnifiedDecisionKind,
    UnifiedResearchDecision,
)


def test_no_actionable_path_requires_unresolved_question() -> None:
    with pytest.raises(ValidationError):
        UnifiedResearchDecision(
            kind=UnifiedDecisionKind.NO_ACTIONABLE_PATH,
            reason="No further actions are needed.",
            unresolved_questions=[],
            action=None,
        )


def test_no_actionable_path_accepts_specific_unresolved_question() -> None:
    decision = UnifiedResearchDecision(
        kind=UnifiedDecisionKind.NO_ACTIONABLE_PATH,
        reason="Evidence is insufficient and no capability can progress.",
        unresolved_questions=["Which production component owns this behavior?"],
        action=None,
    )

    assert decision.kind is UnifiedDecisionKind.NO_ACTIONABLE_PATH
    assert decision.unresolved_questions
