from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.research.task_router import (
    ExecutionRoute,
    ModelTier,
    RoutingDecision,
)
from app.research.unified_agent import UnifiedResearchReasoner


class ExecutionProfile(BaseModel):
    """Concrete execution budget selected after routing."""

    model_config = ConfigDict(extra="forbid")

    route: ExecutionRoute
    model_tier: ModelTier
    model_name: str | None

    max_steps: int = Field(ge=0)
    max_retries: int = Field(ge=0)
    max_decision_output_tokens: int = Field(ge=0)

    max_evidence_items: int = Field(ge=0)
    evidence_snippet_characters: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_route_shape(self) -> "ExecutionProfile":
        if self.route is ExecutionRoute.WORKFLOW:
            if self.model_name is not None:
                raise ValueError("workflow profile must not use an LLM")
            if self.model_tier is not ModelTier.NONE:
                raise ValueError("workflow profile requires model_tier=none")
            if self.max_steps != 0:
                raise ValueError("workflow profile must have max_steps=0")
            return self

        if not self.model_name:
            raise ValueError("agent profiles require model_name")
        if self.max_steps <= 0:
            raise ValueError("agent profiles require positive max_steps")
        if self.max_decision_output_tokens <= 0:
            raise ValueError(
                "agent profiles require positive decision output budget"
            )
        if self.max_evidence_items <= 0:
            raise ValueError(
                "agent profiles require positive evidence item budget"
            )
        if self.evidence_snippet_characters <= 0:
            raise ValueError(
                "agent profiles require positive evidence snippet budget"
            )
        return self


class DefaultExecutionPolicy:
    """
    Day33 concrete policy.

    Model names are deliberately centralized here instead of scattered through
    graph/runtime code so that later A/B policies can be swapped cleanly.
    """

    def __init__(
        self,
        *,
        light_model: str = "deepseek-v4-flash",
        research_model: str = "deepseek-v4-pro",
    ) -> None:
        self._light_model = light_model.strip()
        self._research_model = research_model.strip()
        if not self._light_model or not self._research_model:
            raise ValueError("agent model names must not be empty")

    def resolve(self, decision: RoutingDecision) -> ExecutionProfile:
        if decision.route is ExecutionRoute.WORKFLOW:
            return ExecutionProfile(
                route=ExecutionRoute.WORKFLOW,
                model_tier=ModelTier.NONE,
                model_name=None,
                max_steps=0,
                max_retries=0,
                max_decision_output_tokens=0,
                max_evidence_items=0,
                evidence_snippet_characters=0,
            )

        if decision.route is ExecutionRoute.LIGHT_AGENT:
            return ExecutionProfile(
                route=ExecutionRoute.LIGHT_AGENT,
                model_tier=ModelTier.MEDIUM,
                model_name=self._light_model,
                max_steps=2,
                max_retries=1,
                max_decision_output_tokens=800,
                max_evidence_items=3,
                evidence_snippet_characters=2200,
            )

        return ExecutionProfile(
            route=ExecutionRoute.RESEARCH_AGENT,
            model_tier=ModelTier.LARGE,
            model_name=self._research_model,
            max_steps=5,
            max_retries=2,
            max_decision_output_tokens=1400,
            max_evidence_items=8,
            evidence_snippet_characters=5000,
        )


class ProfiledUnifiedResearchReasoner(UnifiedResearchReasoner):
    """
    Same unified semantic reasoner, but its evidence context is bounded by the
    selected ExecutionProfile.
    """

    def __init__(
        self,
        *,
        provider,
        capabilities: dict[str, str],
        profile: ExecutionProfile,
        max_repairs: int = 1,
    ) -> None:
        if profile.route is ExecutionRoute.WORKFLOW:
            raise ValueError(
                "workflow profile must not construct an LLM reasoner"
            )
        super().__init__(
            provider=provider,
            capabilities=capabilities,
            max_repairs=max_repairs,
        )
        self._profile = profile

    def _build_user_prompt(self, state) -> str:
        pack = state.get("evidence_pack")
        evidence = []
        issues = []
        if pack is not None:
            evidence = [
                {
                    "file_path": item.file_path,
                    "symbol": item.symbol,
                    "line_start": item.line_start,
                    "line_end": item.line_end,
                    "snippet": item.snippet[
                        : self._profile.evidence_snippet_characters
                    ],
                }
                for item in pack.evidence[
                    : self._profile.max_evidence_items
                ]
            ]
            issues = [
                issue.model_dump(mode="json")
                for issue in pack.issues[:5]
            ]

        last_action = state.get("last_action")
        last_tool_result = state.get("last_tool_result")

        payload = {
            "task": state.get("normalized_task", state["query"]),
            "execution_profile": {
                "route": self._profile.route.value,
                "model_tier": self._profile.model_tier.value,
                "max_steps": self._profile.max_steps,
                "max_evidence_items": (
                    self._profile.max_evidence_items
                ),
                "evidence_snippet_characters": (
                    self._profile.evidence_snippet_characters
                ),
            },
            "step_count": state.get("step_count", 0),
            "max_steps": state.get(
                "max_steps",
                self._profile.max_steps,
            ),
            "retry_count": state.get("retry_count", 0),
            "max_retries": state.get(
                "max_retries",
                self._profile.max_retries,
            ),
            "last_action": (
                last_action.model_dump()
                if last_action is not None
                else None
            ),
            "last_tool_result": (
                {
                    "ok": last_tool_result.ok,
                    "error_code": (
                        last_tool_result.error_code.value
                        if last_tool_result.error_code is not None
                        else None
                    ),
                    "truncated": last_tool_result.truncated,
                    "data_keys": sorted(
                        (last_tool_result.data or {}).keys()
                    ),
                }
                if last_tool_result is not None
                else None
            ),
            "evidence": evidence,
            "evidence_issues": issues,
            "allowed_capabilities": self._capabilities,
        }

        return (
            "Current agent state JSON:\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
            + "\n\nReturn one decision JSON."
        )
