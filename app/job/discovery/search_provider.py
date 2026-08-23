from __future__ import annotations

from typing import Protocol

from app.job.schemas import JobRecord, JobSearchSpec

from .models import RawJobResult
from .pipeline import JobDiscoveryPipeline


class JobSearchClient(Protocol):
    async def search(self, spec: JobSearchSpec) -> list[RawJobResult]:
        ...


class SearchJobDiscoveryProvider:
    """Adapter from an external search client to normalized JobRecord values."""

    def __init__(
        self,
        *,
        client: JobSearchClient,
        pipeline: JobDiscoveryPipeline | None = None,
    ) -> None:
        self._client = client
        self._pipeline = pipeline or JobDiscoveryPipeline()

    async def search(self, spec: JobSearchSpec) -> list[JobRecord]:
        raw_jobs = await self._client.search(spec)
        return self._pipeline.process(raw_jobs)
