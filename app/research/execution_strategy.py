from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.research.execution_policy import ExecutionProfile
from app.research.task_router import ExecutionRoute


class EvidenceContextStrategy(StrEnum):
    NONE = "none"
    PREFIX = "prefix"
    QUERY_FOCUSED = "query_focused"


class ExecutionStrategy(BaseModel):
    """
    Day33 final execution strategy.

    Routing chooses the workload class. ExecutionStrategy then controls not
    only model/budget, but also agent autonomy and evidence-context selection.
    """

    model_config = ConfigDict(extra="forbid")

    profile: ExecutionProfile
    deterministic_symbol_first: bool
    evidence_context_strategy: EvidenceContextStrategy

    @property
    def route(self) -> ExecutionRoute:
        return self.profile.route


class DefaultExecutionStrategyPolicy:
    """
    Final Day33 policy derived from controlled experiments.

    WORKFLOW:
      - deterministic
      - no LLM

    LIGHT_AGENT:
      - Flash
      - low step budget
      - deterministic symbol-first fast path when one clear symbol is present
      - query-focused evidence window

    RESEARCH_AGENT:
      - Pro
      - larger bounded dynamic loop
      - no deterministic first-action requirement
      - current baseline context strategy retained until separately evaluated
    """

    def resolve(self, profile: ExecutionProfile) -> ExecutionStrategy:
        if profile.route is ExecutionRoute.WORKFLOW:
            return ExecutionStrategy(
                profile=profile,
                deterministic_symbol_first=False,
                evidence_context_strategy=EvidenceContextStrategy.NONE,
            )

        if profile.route is ExecutionRoute.LIGHT_AGENT:
            return ExecutionStrategy(
                profile=profile,
                deterministic_symbol_first=True,
                evidence_context_strategy=(
                    EvidenceContextStrategy.QUERY_FOCUSED
                ),
            )

        return ExecutionStrategy(
            profile=profile,
            deterministic_symbol_first=False,
            evidence_context_strategy=EvidenceContextStrategy.PREFIX,
        )
