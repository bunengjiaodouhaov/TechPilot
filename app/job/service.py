from .discovery.base import JobDiscoveryProvider
from .query_parser import QueryParser
from .schemas import JobRecord, JobSearchSpec


class JobDiscoveryService:
    """Query -> normalized search spec -> discovered jobs."""

    def __init__(
        self,
        *,
        provider: JobDiscoveryProvider,
        parser: QueryParser | None = None,
    ) -> None:
        self._provider = provider
        self._parser = parser or QueryParser()

    def parse_query(self, query: str) -> JobSearchSpec:
        return self._parser.parse(query)

    async def search(self, query: str) -> list[JobRecord]:
        return await self._provider.search(self.parse_query(query))
