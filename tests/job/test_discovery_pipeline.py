from app.job.discovery.models import RawJobResult
from app.job.discovery.pipeline import JobDiscoveryPipeline


def test_discovery_pipeline_normalizes_filters_and_deduplicates():
    jobs = JobDiscoveryPipeline().process(
        [
            RawJobResult(
                external_id="1",
                title=" AI Engineer ",
                company=" Example ",
                location=" Shanghai ",
                description="Python and RAG experience are required.",
                source="source-a",
            ),
            RawJobResult(
                external_id="2",
                title="AI Engineer",
                company="Example",
                location="Shanghai",
                description="Duplicate copy of the same role with enough JD text.",
                source="source-b",
            ),
            RawJobResult(
                external_id="3",
                title="Bad",
                company="Example",
                location="Shanghai",
                description="short",
                source="source-c",
            ),
        ]
    )

    assert len(jobs) == 1
    assert jobs[0].title == "AI Engineer"
    assert jobs[0].company == "Example"
