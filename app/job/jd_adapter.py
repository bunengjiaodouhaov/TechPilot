from app.jd.extractor import JDExtractorProvider
from app.jd.schemas import StructuredJD

from .schemas import JobRecord


class JobJDService:
    def __init__(self, extractor: JDExtractorProvider) -> None:
        self._extractor = extractor

    async def analyze(self, job: JobRecord) -> StructuredJD:
        return await self._extractor.extract(job.jd_text)
