from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class JobSearchSpec(BaseModel):
    """Normalized search intent consumed by a discovery provider."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    role: str | None = None
    location: str | None = None
    domains: list[str] = Field(default_factory=list)
    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)


class JobRecord(BaseModel):
    """Provider-neutral job record used by JD intelligence."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    company: str = Field(min_length=1)
    title: str = Field(min_length=1)
    location: str | None = None
    jd_text: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_url: str | None = None
    published_at: str | None = None
