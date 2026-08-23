import asyncio

from app.jd.schemas import StructuredJD
from app.job.discovery.mock_provider import MockJobDiscoveryProvider
from app.job.jd_adapter import JobJDService
from app.job.profile import UserCapabilityProfile
from app.job.recommendation_service import JobRecommendationService
from app.job.service import JobDiscoveryService


class FakeJDExtractor:
    async def extract(self, jd_text):
        if "RAG" in jd_text:
            return StructuredJD.model_validate(
                {
                    "title": "AI Engineer",
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
                            "requirement_type": "required",
                            "evidence_span": {
                                "text": "RAG",
                                "start": 11,
                                "end": 14,
                            },
                        },
                    ],
                }
            )
        return StructuredJD(requirements=[])


def _service():
    return JobRecommendationService(
        discovery=JobDiscoveryService(
            provider=MockJobDiscoveryProvider()
        ),
        jd_service=JobJDService(FakeJDExtractor()),
    )


def test_query_only_mode_discovers_and_analyzes_jobs_without_profile():
    result = asyncio.run(
        _service().recommend(
            query="上海 AI Engineer",
            profile=None,
        )
    )

    assert len(result.analyzed_jobs) == 1
    assert result.ranked_jobs is None


def test_optional_profile_mode_ranks_discovered_jobs():
    result = asyncio.run(
        _service().recommend(
            query="上海 AI Engineer",
            profile=UserCapabilityProfile(skills=["Python", "RAG"]),
        )
    )

    assert result.ranked_jobs is not None
    assert result.ranked_jobs[0].score == 1.0
