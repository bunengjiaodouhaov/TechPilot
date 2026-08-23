from app.job.schemas import JobRecord

from .models import RawJobResult


class JobNormalizer:
    def normalize(self, raw: RawJobResult) -> JobRecord:
        return JobRecord(
            id=f"{raw.source}:{raw.external_id}",
            company=raw.company.strip(),
            title=raw.title.strip(),
            location=raw.location.strip() if raw.location else None,
            jd_text=raw.description.strip(),
            source=raw.source.strip(),
            source_url=raw.url,
            published_at=raw.published_at,
        )
