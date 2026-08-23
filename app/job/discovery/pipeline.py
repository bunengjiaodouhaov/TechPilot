from app.job.schemas import JobRecord

from .deduplication import JobDeduplicator
from .models import RawJobResult
from .normalization import JobNormalizer
from .quality import JobQualityFilter


class JobDiscoveryPipeline:
    def __init__(
        self,
        *,
        normalizer: JobNormalizer | None = None,
        quality_filter: JobQualityFilter | None = None,
        deduplicator: JobDeduplicator | None = None,
    ) -> None:
        self._normalizer = normalizer or JobNormalizer()
        self._quality_filter = quality_filter or JobQualityFilter()
        self._deduplicator = deduplicator or JobDeduplicator()

    def process(self, raw_jobs: list[RawJobResult]) -> list[JobRecord]:
        normalized = [self._normalizer.normalize(job) for job in raw_jobs]
        valid = self._quality_filter.filter(normalized)
        return self._deduplicator.deduplicate(valid)
