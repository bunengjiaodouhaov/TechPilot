from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.harness.evidence_pack import EvidencePack
from app.harness.tool_runtime import ToolResult


class ResearchStep(BaseModel):
    """One research objective; it is intentionally not a ToolCall."""

    model_config = ConfigDict(extra="forbid")

    objective: str
    source_requirement: str | None = None

    @field_validator("objective")
    @classmethod
    def objective_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("objective must not be empty")
        return normalized

    @field_validator("source_requirement")
    @classmethod
    def source_requirement_must_not_be_blank(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("source_requirement must not be empty")
        return normalized


class ResearchAction(BaseModel):
    """A runtime ACT decision, separate from the research plan."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str

    @field_validator("tool_name", "reason")
    @classmethod
    def required_text_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized


class DecisionFailureCode(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_ERROR = "upstream_error"
    NETWORK_ERROR = "network_error"
    AUTH_ERROR = "auth_error"
    REQUEST_ERROR = "request_error"
    INVALID_RESPONSE = "invalid_response"


class ResearchDecisionFailure(BaseModel):
    """Structured failure emitted by the semantic decision provider."""

    model_config = ConfigDict(extra="forbid")

    code: DecisionFailureCode
    retryable: bool
    message: str
    status_code: int | None = None


class VerificationResult(BaseModel):
    """Evidence sufficiency result; continuation is a control-layer decision."""

    model_config = ConfigDict(extra="forbid")

    sufficient: bool
    reason: str
    unresolved_questions: list[str] = Field(default_factory=list)

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason must not be empty")
        return normalized


class TerminationReason(StrEnum):
    COMPLETED = "completed"
    MAX_STEPS = "max_steps"
    RETRY_EXHAUSTED = "retry_exhausted"
    PERMANENT_FAILURE = "permanent_failure"
    NO_ACTIONABLE_PATH = "no_actionable_path"



class ActionExecutionOutcome(BaseModel):
    """Action-level result visible to semantic reasoning.

    This is distinct from ToolResult because one research action may be a
    composite capability that invokes several primitive tools.
    """

    model_config = ConfigDict(extra="forbid")

    capability: str
    tool_result_present: bool
    tool_result_ok: bool | None = None
    evidence_returned_count: int = Field(ge=0)
    new_evidence_count: int = Field(ge=0)
    issue_count: int = Field(ge=0)
    retry_count_after: int = Field(ge=0)
    termination_reason: TerminationReason | None = None


class ResearchContext(TypedDict, total=False):
    """Run-scoped metadata/dependencies that must not pollute ResearchState."""

    trace_id: str


class _RequiredResearchState(TypedDict):
    query: str


class ResearchState(_RequiredResearchState, total=False):
    """Minimal mutable state needed by later nodes and routing decisions."""

    normalized_task: str
    plan: list[ResearchStep]
    current_step: int

    # Needed by later ACT turns to avoid blind or semantic repetition.
    last_action: ResearchAction | None
    action_history: list[ResearchAction]
    last_tool_result: ToolResult | None
    last_action_outcome: ActionExecutionOutcome | None
    evidence_pack: EvidencePack | None
    verification: VerificationResult | None

    step_count: int
    max_steps: int
    retry_count: int
    max_retries: int

    # Consecutive semantic-decision provider failures are a distinct retry
    # domain from tool/runtime retries.
    decision_retry_count: int
    max_decision_retries: int
    decision_failure: ResearchDecisionFailure | None

    termination_reason: TerminationReason | None
    incomplete: bool
    final_answer: str | None
