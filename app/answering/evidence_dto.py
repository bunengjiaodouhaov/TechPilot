from __future__ import annotations

from enum import Enum
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationInfo,
    field_validator,
    model_validator,
)


class EvidenceState(str, Enum):
    """Overall support state for one verification target."""

    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    CONFLICTING = "conflicting"


class EvidenceReason(str, Enum):
    """Structured reasons explaining a non-sufficient evidence state."""

    NO_EVIDENCE = "no_evidence"
    SUBJECT_MISMATCH = "subject_mismatch"
    ATTRIBUTE_MISSING = "attribute_missing"
    RELATION_MISSING = "relation_missing"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


class EvidenceItem(BaseModel):
    """One evidence fragment with provider-neutral provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    text: str
    source_type: str
    source_ref: str
    title: str | None = None
    locator: str | None = None

    @field_validator("source_id", "text", "source_type", "source_ref")
    @classmethod
    def validate_required_text(
        cls,
        value: str,
        info: ValidationInfo,
    ) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"evidence {info.field_name} must not be empty")
        if info.field_name == "text":
            return value
        return normalized


class EvidenceVerificationInput(BaseModel):
    """Structured input for evidence sufficiency verification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: str
    evidence: tuple[EvidenceItem, ...]

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("target must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_source_ids(self) -> Self:
        source_ids = [item.source_id.strip() for item in self.evidence]
        if len(set(source_ids)) != len(source_ids):
            duplicates = sorted(
                source_id
                for source_id in set(source_ids)
                if source_ids.count(source_id) > 1
            )
            raise ValueError(
                "duplicate evidence source_id: " + ", ".join(duplicates)
            )
        return self


class EvidenceVerificationResult(BaseModel):
    """Structured evidence state returned by a verifier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: EvidenceState
    reasons: tuple[EvidenceReason, ...]
    supporting_source_ids: tuple[str, ...]
    conflicting_source_ids: tuple[str, ...]
    explanation: str

    @field_validator("supporting_source_ids", "conflicting_source_ids")
    @classmethod
    def validate_source_ids(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        normalized = tuple(source_id.strip() for source_id in value)
        if any(not source_id for source_id in normalized):
            raise ValueError(
                f"{info.field_name} must contain non-empty strings"
            )
        if len(set(normalized)) != len(normalized):
            raise ValueError(f"{info.field_name} contain duplicates")
        return normalized

    @field_validator("reasons")
    @classmethod
    def validate_reasons(
        cls,
        value: tuple[EvidenceReason, ...],
    ) -> tuple[EvidenceReason, ...]:
        if len(set(value)) != len(value):
            raise ValueError("evidence verification reasons contain duplicates")
        return value

    @field_validator("explanation")
    @classmethod
    def validate_explanation(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("explanation must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_state_invariants(self) -> Self:
        overlap = sorted(
            set(self.supporting_source_ids) & set(self.conflicting_source_ids)
        )
        if overlap:
            raise ValueError(
                "evidence verification source roles overlap: "
                + ", ".join(overlap)
            )

        if self.state is EvidenceState.SUFFICIENT:
            if (
                self.reasons
                or self.conflicting_source_ids
                or not self.supporting_source_ids
            ):
                raise ValueError(
                    "sufficient evidence state is inconsistent with reasons or sources"
                )
            return self

        if self.state is EvidenceState.INSUFFICIENT:
            if len(self.reasons) != 1 or self.conflicting_source_ids:
                raise ValueError(
                    "insufficient evidence state must contain exactly one primary reason"
                )
            if EvidenceReason.CONFLICTING_EVIDENCE in self.reasons:
                raise ValueError(
                    "insufficient evidence state cannot use conflicting_evidence reason"
                )
            return self

        if (
            EvidenceReason.CONFLICTING_EVIDENCE not in self.reasons
            or not self.conflicting_source_ids
        ):
            raise ValueError(
                "conflicting evidence state is inconsistent with reasons or sources"
            )
        return self
