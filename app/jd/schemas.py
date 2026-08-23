from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RequirementCategory(StrEnum):
    TECHNICAL = "technical"
    EXPERIENCE = "experience"
    DOMAIN = "domain"
    EDUCATION = "education"
    SOFT_SKILL = "soft_skill"
    RESPONSIBILITY = "responsibility"
    OTHER = "other"


class RequirementType(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    UNCLEAR = "unclear"


class EvidenceSpan(BaseModel):
    """Exact source span in the original JD. ``end`` is exclusive."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> "EvidenceSpan":
        if self.end <= self.start:
            raise ValueError("evidence span end must be greater than start")
        return self


class JDRequirement(BaseModel):
    """One requirement grounded in an exact JD source span."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)
    normalized_skill: str | None = None
    category: RequirementCategory
    requirement_type: RequirementType = RequirementType.UNCLEAR
    years_min: float | None = Field(default=None, ge=0)
    years_max: float | None = Field(default=None, ge=0)
    evidence_span: EvidenceSpan

    @model_validator(mode="after")
    def validate_year_range(self) -> "JDRequirement":
        if (
            self.years_min is not None
            and self.years_max is not None
            and self.years_max < self.years_min
        ):
            raise ValueError("years_max must be greater than or equal to years_min")
        return self


class StructuredJD(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    company: str | None = None
    requirements: list[JDRequirement] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "StructuredJD":
        ids = [item.id for item in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("requirement ids must be unique")
        return self
