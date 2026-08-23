from __future__ import annotations

from dataclasses import dataclass

from app.job.schemas import JobSearchSpec

from ..discovery.base import JobDiscoveryProvider


@dataclass(frozen=True, slots=True)
class DiscoveryMetrics:
    result_count: int
    role_hit: bool
    location_precision: float


class DiscoveryEvaluator:
    def __init__(self, provider: JobDiscoveryProvider) -> None:
        self._provider = provider

    async def evaluate(
        self,
        *,
        spec: JobSearchSpec,
        expected_role: str | None,
    ) -> DiscoveryMetrics:
        jobs = await self._provider.search(spec)
        role_hit = (
            True
            if expected_role is None
            else any(
                expected_role.casefold() in job.title.casefold()
                for job in jobs
            )
        )
        location_precision = (
            1.0
            if not jobs or spec.location is None
            else sum(
                (job.location or "").casefold() == spec.location.casefold()
                for job in jobs
            )
            / len(jobs)
        )
        return DiscoveryMetrics(
            result_count=len(jobs),
            role_hit=role_hit,
            location_precision=location_precision,
        )
