import pytest
from pydantic import ValidationError

from app.jd.schemas import StructuredJD


def test_structured_jd_accepts_grounded_requirement():
    payload = {
        "title": "AI Engineer",
        "company": "Example",
        "requirements": [
            {
                "id": "req-1",
                "raw_text": "Python is required",
                "normalized_skill": "Python",
                "category": "technical",
                "requirement_type": "required",
                "years_min": None,
                "years_max": None,
                "evidence_span": {
                    "text": "Python is required",
                    "start": 0,
                    "end": 18,
                },
            }
        ],
    }

    result = StructuredJD.model_validate(payload)

    assert result.requirements[0].normalized_skill == "Python"


def test_requirement_ids_must_be_unique():
    requirement = {
        "id": "req-1",
        "raw_text": "Python",
        "normalized_skill": "Python",
        "category": "technical",
        "requirement_type": "required",
        "evidence_span": {"text": "Python", "start": 0, "end": 6},
    }

    with pytest.raises(ValidationError):
        StructuredJD.model_validate(
            {"requirements": [requirement, dict(requirement)]}
        )
