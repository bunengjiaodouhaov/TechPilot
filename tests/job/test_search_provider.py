import asyncio

from app.job.discovery.models import RawJobResult
from app.job.discovery.search_provider import SearchJobDiscoveryProvider
from app.job.schemas import JobSearchSpec


class FakeSearchClient:
    async def search(self, spec):
        return [
            RawJobResult(
                external_id="42",
                title="LLM Engineer",
                company="Example AI",
                location="Shanghai",
                description="Python and RAG experience are required for this role.",
                source="fake-search",
                url="https://example.test/job/42",
            )
        ]


def test_search_provider_returns_normalized_job_records():
    provider = SearchJobDiscoveryProvider(
        client=FakeSearchClient()
    )
    jobs = asyncio.run(
        provider.search(
            JobSearchSpec(
                query="Shanghai LLM Engineer",
                role="LLM Engineer",
                location="Shanghai",
            )
        )
    )

    assert len(jobs) == 1
    assert jobs[0].source == "fake-search"
    assert jobs[0].source_url == "https://example.test/job/42"
