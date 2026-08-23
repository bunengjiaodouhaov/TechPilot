from __future__ import annotations

from .extractor import JDExtractorProvider
from .schemas import StructuredJD


class JDService:
    def __init__(self, extractor: JDExtractorProvider) -> None:
        self._extractor = extractor

    async def extract(self, jd_text: str) -> StructuredJD:
        return await self._extractor.extract(jd_text)
