from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class UserCapabilityProfile(BaseModel):
    """Optional normalized capability input, e.g. derived from a resume later."""

    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list)
