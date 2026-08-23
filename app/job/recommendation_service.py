from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.jd.schemas import StructuredJD

from .jd_adapter import JobJDService
from .matching import JobMatcher
from .profile import UserCapabilityProfile
from .ranking import JobRanker, RankedJob
from .schemas import JobRecord
from .service import JobDiscoveryService


class AnalyzedJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: JobRecord
    jd: StructuredJD


class JobRecommendationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analyzed_jobs: list[AnalyzedJob] = Field(default_factory=list)
    ranked_jobs: list[RankedJob] | None = None


class JobRecommendationService:
    """Discover jobs automatically; rank only when a profile is supplied."""

    def __init__(
        self,
        *,
        discovery: JobDiscoveryService,
        jd_service: JobJDService,
        matcher: JobMatcher | None = None,
        ranker: JobRanker | None = None,
    ) -> None:
        self._discovery = discovery
        self._jd_service = jd_service
        self._matcher = matcher or JobMatcher()
        self._ranker = ranker or JobRanker()

    async def recommend(
        self,
        *,
        query: str,
        profile: UserCapabilityProfile | None = None,
    ) -> JobRecommendationResult:
        jobs = await self._discovery.search(query)
        analyzed = [
            AnalyzedJob(job=job, jd=await self._jd_service.analyze(job))
            for job in jobs
        ]

        if profile is None:
            return JobRecommendationResult(analyzed_jobs=analyzed)

        ranked = self._ranker.rank(
            [
                (
                    item.job,
                    self._matcher.match(jd=item.jd, profile=profile),
                )
                for item in analyzed
            ]
        )
        return JobRecommendationResult(
            analyzed_jobs=analyzed,
            ranked_jobs=ranked,
        )
