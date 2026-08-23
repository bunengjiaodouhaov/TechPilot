from app.job.schemas import JobRecord


class JobDeduplicator:
    """Deterministic first-pass duplicate removal."""

    def deduplicate(self, jobs: list[JobRecord]) -> list[JobRecord]:
        seen: set[tuple[str, str, str]] = set()
        unique: list[JobRecord] = []

        for job in jobs:
            key = (
                job.company.casefold().strip(),
                job.title.casefold().strip(),
                (job.location or "").casefold().strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(job)

        return unique
