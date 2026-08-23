import pytest

from app.jd.extractor import (
    JDExtractionValidationError,
    bounded_structural_repair,
    validate_structured_jd,
)


def _payload(*, start=0, end=6, text="Python", requirement_id="req-1"):
    return {
        "title": "Backend Engineer",
        "company": None,
        "requirements": [
            {
                "id": requirement_id,
                "raw_text": text,
                "normalized_skill": "Python",
                "category": "technical",
                "requirement_type": "required",
                "evidence_span": {
                    "text": text,
                    "start": start,
                    "end": end,
                },
            }
        ],
    }


def test_evidence_span_must_bind_to_original_jd():
    jd_text = "Python backend experience required."
    result = validate_structured_jd(jd_text=jd_text, payload=_payload())
    assert result.requirements[0].evidence_span.text == "Python"


def test_invalid_evidence_span_fails_closed():
    jd_text = "Python backend experience required."
    with pytest.raises(JDExtractionValidationError):
        validate_structured_jd(
            jd_text=jd_text,
            payload=_payload(start=7, end=13),
        )


def test_structural_repair_only_normalizes_integer_id():
    repaired = bounded_structural_repair(_payload(requirement_id=1))
    assert repaired["requirements"][0]["id"] == "1"


def test_structural_repair_does_not_invent_evidence_span():
    payload = _payload()
    payload["requirements"][0]["evidence_span"] = "Python"
    repaired = bounded_structural_repair(payload)
    assert repaired["requirements"][0]["evidence_span"] == "Python"
