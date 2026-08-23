from __future__ import annotations

from typing import Protocol

from app.job.schemas import JobRecord, JobSearchSpec


class JobDiscoveryProvider(Protocol):
    async def search(self, spec: JobSearchSpec) -> list[JobRecord]:
        ...
