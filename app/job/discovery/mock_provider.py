from app.job.schemas import JobRecord, JobSearchSpec


class MockJobDiscoveryProvider:
    """Test-only provider. Do not wire this into production dependencies."""

    async def search(self, spec: JobSearchSpec) -> list[JobRecord]:
        candidates = [
            JobRecord(
                id="mock:ai-1",
                company="Example AI",
                title="AI Engineer",
                location="Shanghai",
                jd_text="Python and RAG experience are required for this AI application role.",
                source="mock",
            ),
            JobRecord(
                id="mock:backend-1",
                company="Example Backend",
                title="Backend Engineer",
                location="Shanghai",
                jd_text="Python and PostgreSQL experience are required for backend services.",
                source="mock",
            ),
        ]
        result = candidates
        if spec.role:
            result = [
                job for job in result
                if spec.role.casefold() in job.title.casefold()
            ]
        if spec.location:
            result = [
                job for job in result
                if (job.location or "").casefold() == spec.location.casefold()
            ]
        return result
