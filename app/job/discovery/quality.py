from app.job.schemas import JobRecord


class JobQualityFilter:
    """Reject records that cannot support downstream JD analysis."""

    def filter(self, jobs: list[JobRecord]) -> list[JobRecord]:
        return [
            job
            for job in jobs
            if job.title.strip()
            and job.company.strip()
            and len(job.jd_text.strip()) >= 20
        ]
