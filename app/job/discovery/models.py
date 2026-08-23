from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RawJobResult(BaseModel):
    """Untrusted provider payload before normalization."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    company: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source: str = Field(min_length=1)
    location: str | None = None
    url: str | None = None
    published_at: str | None = None
