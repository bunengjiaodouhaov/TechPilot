from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.harness.tool_runtime import ToolErrorCode
from app.repository.code_evidence import CodeEvidence


class EvidenceIssueKind(StrEnum):
    TOOL_UNAVAILABLE = "tool_unavailable"
    TOOL_FAILURE = "tool_failure"
    TOOL_TRUNCATED = "tool_truncated"
    PARSE_ERROR = "parse_error"
    EVIDENCE_LIMIT = "evidence_limit"
    PROVENANCE_MISMATCH = "provenance_mismatch"


class EvidencePackIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: EvidenceIssueKind
    tool_name: str | None = None
    error_code: ToolErrorCode | None = None
    file_path: str | None = None
    count: int | None = Field(default=None, ge=1)


class EvidencePack(BaseModel):
    """Minimal auditable evidence handoff from repository exploration."""

    model_config = ConfigDict(extra="forbid")

    query: str
    task_intent: str
    evidence: list[CodeEvidence] = Field(default_factory=list)
    provenance_integrity: bool
    incomplete: bool
    issues: list[EvidencePackIssue] = Field(default_factory=list)

    @field_validator("query", "task_intent")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized
