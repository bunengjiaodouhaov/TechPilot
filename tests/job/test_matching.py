from app.jd.schemas import StructuredJD
from app.job.matching import JobMatcher
from app.job.profile import UserCapabilityProfile


def test_optional_profile_matching_weights_required_requirements_more():
    jd = StructuredJD.model_validate(
        {
            "requirements": [
                {
                    "id": "req-1",
                    "raw_text": "Python",
                    "normalized_skill": "Python",
                    "category": "technical",
                    "requirement_type": "required",
                    "evidence_span": {
                        "text": "Python",
                        "start": 0,
                        "end": 6,
                    },
                },
                {
                    "id": "req-2",
                    "raw_text": "RAG",
                    "normalized_skill": "RAG",
                    "category": "technical",
                    "requirement_type": "preferred",
                    "evidence_span": {
                        "text": "RAG",
                        "start": 7,
                        "end": 10,
                    },
                },
            ]
        }
    )

    report = JobMatcher().match(
        jd=jd,
        profile=UserCapabilityProfile(skills=["Python"]),
    )

    assert report.score == 2 / 3
    assert report.matched_skills == ["Python"]
    assert report.missing_preferred_skills == ["RAG"]
