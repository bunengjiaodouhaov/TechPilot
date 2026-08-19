from __future__ import annotations

import inspect
import json
from enum import StrEnum
from typing import Any, Awaitable, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.harness.evidence_pack import EvidencePack
from app.harness.tool_runtime import ToolErrorCode, ToolResult
from app.research.contracts import (
    ResearchAction,
    ResearchContext,
    ResearchState,
    TerminationReason,
    VerificationResult,
)
from app.research.decision_llm import ResearchDecisionProvider
from app.research.execution import ResearchActionExecutor


class UnifiedDecisionKind(StrEnum):
    ACT = "act"
    COMPLETE = "complete"
    NO_ACTIONABLE_PATH = "no_actionable_path"


class UnifiedResearchDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: UnifiedDecisionKind
    reason: str
    unresolved_questions: list[str] = Field(default_factory=list)
    action: ResearchAction | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "UnifiedResearchDecision":
        if self.kind is UnifiedDecisionKind.ACT and self.action is None:
            raise ValueError("ACT decision requires action")
        if self.kind is not UnifiedDecisionKind.ACT and self.action is not None:
            raise ValueError("non-ACT decision must not contain action")
        if (
            self.kind is UnifiedDecisionKind.COMPLETE
            and self.unresolved_questions
        ):
            raise ValueError(
                "COMPLETE decision cannot retain unresolved questions"
            )
        return self


class UnifiedAgentState(ResearchState, total=False):
    decision: UnifiedResearchDecision | None


UNIFIED_REASONER_SYSTEM_PROMPT = """\
You are the semantic reasoning core of a bounded research agent.

You perform three semantic jobs together:
1. understand the research objective,
2. judge whether the CURRENT authoritative evidence is sufficient,
3. if insufficient, choose at most one next allowed capability.

You do NOT:
- execute tools,
- decide permissions,
- override schema validation,
- control timeout/retry budgets,
- override max_steps,
- treat retrieval candidates as authoritative evidence.

Decision rules:
- If current authoritative evidence directly supports every material part of
  the task, return kind="complete".
- If evidence is insufficient and an allowed capability can make progress,
  return kind="act" with exactly one ResearchAction.
- If evidence is insufficient and no allowed capability can make progress,
  return kind="no_actionable_path".
- Use the previous action and evidence to avoid blindly repeating an
  unsuccessful action with identical arguments.
- Use only capability names explicitly listed in allowed_capabilities.

Return exactly one JSON object:
{
  "kind": "act" | "complete" | "no_actionable_path",
  "reason": "why this decision is correct",
  "unresolved_questions": ["specific missing fact", "..."],
  "action": {
    "tool_name": "allowed capability",
    "arguments": {},
    "reason": "why this action addresses the evidence gap"
  } | null
}
"""


class UnifiedResearchReasoner:
    def __init__(
        self,
        *,
        provider: ResearchDecisionProvider,
        capabilities: dict[str, str],
        max_repairs: int = 1,
    ) -> None:
        if not capabilities:
            raise ValueError("capabilities must not be empty")
        if max_repairs < 0:
            raise ValueError("max_repairs must be >= 0")

        self._provider = provider
        self._capabilities = {
            name.strip(): description.strip()
            for name, description in capabilities.items()
        }
        if any(
            not name or not description
            for name, description in self._capabilities.items()
        ):
            raise ValueError(
                "capability names/descriptions must not be blank"
            )
        self._max_repairs = max_repairs

    async def decide(
        self,
        state: UnifiedAgentState,
    ) -> UnifiedResearchDecision:
        payload = await self._provider.generate_json(
            system_prompt=UNIFIED_REASONER_SYSTEM_PROMPT,
            user_prompt=self._build_user_prompt(state),
        )

        last_error: Exception | None = None

        for repair_index in range(self._max_repairs + 1):
            try:
                decision = UnifiedResearchDecision.model_validate(payload)
                self._validate_decision(decision, state)
                return decision
            except (ValidationError, ValueError) as exc:
                last_error = exc

            if repair_index >= self._max_repairs:
                break

            payload = await self._provider.generate_json(
                system_prompt=UNIFIED_REASONER_SYSTEM_PROMPT,
                user_prompt=(
                    "The previous decision violated the agent decision "
                    "contract. It has NOT been executed.\n\n"
                    "Invalid JSON:\n"
                    f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
                    f"{self._build_user_prompt(state)}\n\n"
                    "Return one corrected decision JSON."
                ),
            )

        raise ValueError(
            "LLM returned an invalid unified research decision "
            "after bounded repair"
        ) from last_error

    def _validate_decision(
        self,
        decision: UnifiedResearchDecision,
        state: UnifiedAgentState,
    ) -> None:
        if (
            decision.action is not None
            and decision.action.tool_name not in self._capabilities
        ):
            raise ValueError("selected capability is not allowed")

        if decision.kind is UnifiedDecisionKind.COMPLETE:
            pack = state.get("evidence_pack")
            if pack is None or not pack.evidence:
                raise ValueError(
                    "cannot complete without authoritative evidence"
                )

    def _build_user_prompt(self, state: UnifiedAgentState) -> str:
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
                    "snippet": item.snippet[:5000],
                }
                for item in pack.evidence[:8]
            ]
            issues = [
                issue.model_dump(mode="json")
                for issue in pack.issues[:5]
            ]

        last_action = state.get("last_action")
        last_tool_result = state.get("last_tool_result")

        payload = {
            "task": state.get("normalized_task", state["query"]),
            "step_count": state.get("step_count", 0),
            "max_steps": state.get("max_steps", 4),
            "retry_count": state.get("retry_count", 0),
            "max_retries": state.get("max_retries", 1),
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


class UnifiedReasoner(Protocol):
    def decide(
        self,
        state: UnifiedAgentState,
    ) -> UnifiedResearchDecision | Awaitable[UnifiedResearchDecision]:
        ...


class ResearchFinalizer(Protocol):
    def finalize(self, state: ResearchState) -> str:
        ...


PERMANENT_TOOL_ERRORS = frozenset(
    {
        ToolErrorCode.INVALID_INPUT,
        ToolErrorCode.PERMISSION_DENIED,
        ToolErrorCode.EXECUTION_ERROR,
        ToolErrorCode.INVALID_OUTPUT,
    }
)


class AccumulatingActionExecutor:
    """Decorator that preserves authoritative evidence across ACT turns."""

    def __init__(self, inner: ResearchActionExecutor) -> None:
        self._inner = inner

    async def execute(
        self,
        *,
        action: ResearchAction,
        state: ResearchState,
        trace_metadata: dict[str, Any],
    ) -> dict:
        updates = await self._inner.execute(
            action=action,
            state=state,
            trace_metadata=trace_metadata,
        )

        incoming = updates.get("evidence_pack")
        existing = state.get("evidence_pack")
        if isinstance(incoming, EvidencePack):
            updates["evidence_pack"] = merge_evidence_packs(
                existing=existing,
                incoming=incoming,
                state=state,
            )

        return updates


def merge_evidence_packs(
    *,
    existing: EvidencePack | None,
    incoming: EvidencePack,
    state: ResearchState,
) -> EvidencePack:
    if existing is None:
        return incoming.model_copy(
            update={
                "query": state["query"],
                "task_intent": state.get(
                    "normalized_task",
                    incoming.task_intent,
                ),
            }
        )

    evidence_by_key = {}
    for item in [*existing.evidence, *incoming.evidence]:
        key = (
            item.repository,
            item.file_path,
            item.symbol,
            item.line_start,
            item.line_end,
        )
        evidence_by_key[key] = item

    issue_by_key = {}
    for issue in [*existing.issues, *incoming.issues]:
        dumped = issue.model_dump(mode="json")
        key = tuple(
            sorted((name, str(value)) for name, value in dumped.items())
        )
        issue_by_key[key] = issue

    return EvidencePack(
        query=state["query"],
        task_intent=state.get(
            "normalized_task",
            incoming.task_intent,
        ),
        evidence=list(evidence_by_key.values()),
        provenance_integrity=(
            existing.provenance_integrity
            and incoming.provenance_integrity
        ),
        incomplete=existing.incomplete or incoming.incomplete,
        issues=list(issue_by_key.values()),
    )


class UnifiedResearchNodes:
    def __init__(
        self,
        *,
        executor: ResearchActionExecutor,
        reasoner: UnifiedReasoner,
        finalizer: ResearchFinalizer,
    ) -> None:
        self._executor = AccumulatingActionExecutor(executor)
        self._reasoner = reasoner
        self._finalizer = finalizer

    def normalize(self, state: UnifiedAgentState) -> dict:
        query = state["query"].strip()
        if not query:
            raise ValueError("query must not be empty")

        return {
            "normalized_task": " ".join(query.split()),
            "step_count": state.get("step_count", 0),
            "max_steps": state.get("max_steps", 4),
            "retry_count": state.get("retry_count", 0),
            "max_retries": state.get("max_retries", 1),
            "last_action": state.get("last_action"),
            "decision": None,
            "termination_reason": None,
            "incomplete": False,
            "final_answer": None,
        }

    async def decide(self, state: UnifiedAgentState) -> dict:
        control_reason = deterministic_termination(state)
        if control_reason is not None:
            return {
                "termination_reason": control_reason,
                "decision": None,
            }

        decision_or_awaitable = self._reasoner.decide(state)
        decision = (
            await decision_or_awaitable
            if inspect.isawaitable(decision_or_awaitable)
            else decision_or_awaitable
        )

        if decision.kind is UnifiedDecisionKind.COMPLETE:
            return {
                "decision": decision,
                "verification": VerificationResult(
                    sufficient=True,
                    reason=decision.reason,
                    unresolved_questions=[],
                ),
                "termination_reason": TerminationReason.COMPLETED,
            }

        if decision.kind is UnifiedDecisionKind.NO_ACTIONABLE_PATH:
            return {
                "decision": decision,
                "verification": VerificationResult(
                    sufficient=False,
                    reason=decision.reason,
                    unresolved_questions=decision.unresolved_questions,
                ),
                "termination_reason": (
                    TerminationReason.NO_ACTIONABLE_PATH
                ),
            }

        return {
            "decision": decision,
            "verification": VerificationResult(
                sufficient=False,
                reason=decision.reason,
                unresolved_questions=decision.unresolved_questions,
            ),
        }

    async def act(
        self,
        state: UnifiedAgentState,
        runtime: Runtime[ResearchContext],
    ) -> dict:
        decision = state.get("decision")
        if (
            decision is None
            or decision.kind is not UnifiedDecisionKind.ACT
            or decision.action is None
        ):
            raise ValueError("ACT node requires an ACT decision")

        action = decision.action
        trace_metadata: dict[str, Any] = {
            "decision_reason": decision.reason,
            "research_action": action.tool_name,
            "research_action_arguments": action.arguments,
        }
        if runtime.context:
            trace_id = runtime.context.get("trace_id")
            if trace_id:
                trace_metadata["trace_id"] = trace_id

        updates = await self._executor.execute(
            action=action,
            state=state,
            trace_metadata=trace_metadata,
        )
        return {
            **updates,
            "last_action": action,
            "decision": None,
        }

    def finalize(self, state: UnifiedAgentState) -> dict:
        reason = (
            state.get("termination_reason")
            or deterministic_termination(state)
            or TerminationReason.NO_ACTIONABLE_PATH
        )
        final_state = dict(state)
        final_state["termination_reason"] = reason
        final_state["incomplete"] = reason is not TerminationReason.COMPLETED

        return {
            "termination_reason": reason,
            "incomplete": reason is not TerminationReason.COMPLETED,
            "final_answer": self._finalizer.finalize(final_state),
        }


def deterministic_termination(
    state: UnifiedAgentState,
) -> TerminationReason | None:
    if state.get("step_count", 0) >= state.get("max_steps", 4):
        return TerminationReason.MAX_STEPS

    result: ToolResult | None = state.get("last_tool_result")
    if (
        result is not None
        and not result.ok
        and result.error_code in PERMANENT_TOOL_ERRORS
    ):
        return TerminationReason.PERMANENT_FAILURE

    if state.get("retry_count", 0) > state.get("max_retries", 1):
        return TerminationReason.RETRY_EXHAUSTED

    return None


def build_unified_research_graph(
    *,
    executor: ResearchActionExecutor,
    reasoner: UnifiedReasoner,
    finalizer: ResearchFinalizer,
):
    nodes = UnifiedResearchNodes(
        executor=executor,
        reasoner=reasoner,
        finalizer=finalizer,
    )

    graph = StateGraph(
        UnifiedAgentState,
        context_schema=ResearchContext,
    )
    graph.add_node("normalize", nodes.normalize)
    graph.add_node("decide", nodes.decide)
    graph.add_node("act", nodes.act)
    graph.add_node("finalize", nodes.finalize)

    graph.add_edge(START, "normalize")
    graph.add_edge("normalize", "decide")
    graph.add_conditional_edges(
        "decide",
        lambda state: (
            "finalize"
            if state.get("termination_reason") is not None
            else "act"
        ),
        {
            "act": "act",
            "finalize": "finalize",
        },
    )
    graph.add_edge("act", "decide")
    graph.add_edge("finalize", END)

    return graph.compile()
