from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .schemas import EvidenceSpan, RequirementCategory, RequirementType


class RequirementAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: str | None = None
    category: RequirementCategory
    requirement_type: RequirementType
    evidence_span: EvidenceSpan


class JDAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    requirements: list[RequirementAnnotation] = Field(default_factory=list)
