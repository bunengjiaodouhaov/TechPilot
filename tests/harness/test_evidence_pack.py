import pytest
from pydantic import ValidationError

from app.harness.evidence_pack import EvidencePack


def test_evidence_pack_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        EvidencePack.model_validate(
            {
                "query": "query",
                "task_intent": "understand code",
                "evidence": [],
                "provenance_integrity": True,
                "incomplete": False,
                "issues": [],
                "invented": "field",
            }
        )


def test_evidence_pack_rejects_blank_query() -> None:
    with pytest.raises(ValidationError):
        EvidencePack(
            query=" ",
            task_intent="understand code",
            evidence=[],
            provenance_integrity=True,
            incomplete=False,
            issues=[],
        )
