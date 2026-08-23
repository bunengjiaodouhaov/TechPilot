from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .matching import JobMatchReport
from .schemas import JobRecord


class RankedJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: JobRecord
    score: float = Field(ge=0, le=1)
    matched_skills: list[str] = Field(default_factory=list)
    missing_required_skills: list[str] = Field(default_factory=list)
    missing_preferred_skills: list[str] = Field(default_factory=list)


class JobRanker:
    def rank(
        self,
        candidates: list[tuple[JobRecord, JobMatchReport]],
    ) -> list[RankedJob]:
        ranked = [
            RankedJob(
                job=job,
                score=report.score,
                matched_skills=report.matched_skills,
                missing_required_skills=report.missing_required_skills,
                missing_preferred_skills=report.missing_preferred_skills,
            )
            for job, report in candidates
        ]
        return sorted(ranked, key=lambda item: item.score, reverse=True)
