from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.research.contracts import (
    ResearchAction,
    ResearchState,
    ResearchStep,
)
from app.research.decision_llm import ResearchDecisionProvider


class ResearchDecisionValidationError(ValueError):
    """Raised when an LLM decision remains invalid after bounded repair."""


class ResearchPlanDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[ResearchStep] = Field(min_length=1, max_length=1)


class ResearchActionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: ResearchAction | None


PLANNER_SYSTEM_PROMPT = """\
You are the planning component of a bounded research agent.

Your job is semantic planning only.
Do not choose tools.
Do not execute tools.
Do not decide permissions, retries, or termination.

For the current Day32 control loop, produce EXACTLY ONE research objective.
If the task contains multiple sub-questions, merge them into one bounded
business objective rather than returning multiple steps.

Return JSON exactly in this shape:
{
  "steps": [
    {
      "objective": "one merged objective describing what must be established",
      "source_requirement": "what kind of authoritative evidence is required"
    }
  ]
}
"""


ACTION_SYSTEM_PROMPT = """\
You are the action-selection component of a bounded research agent.

Choose at most one next capability using the current research objective,
materialized evidence, verifier gaps, the previous action, and the allowed
capability list.

Rules:
1. Return only one JSON object.
2. Use only an allowed capability name.
3. Do not execute anything yourself.
4. Do not decide permission, retry budgets, or termination.
5. Retrieval candidates are not authoritative evidence until the capability
   materializes source content.
6. If evidence is insufficient, choose an action that directly addresses the
   unresolved gap when an allowed capability can do so.
7. Do NOT blindly repeat the same action with the same arguments when it
   produced no evidence. Change the search strategy or focused query when the
   capability contract allows it.
8. Respect the exact argument semantics documented in allowed_capabilities.
9. If no allowed capability can make progress, return {"action": null}.

Return JSON in one of these shapes:
{
  "action": {
    "tool_name": "allowed capability name",
    "arguments": {},
    "reason": "why this is the next best action"
  }
}

or:
{"action": null}
"""


class LLMResearchPlanner:
    def __init__(
        self,
        *,
        provider: ResearchDecisionProvider,
        max_repairs: int = 1,
    ) -> None:
        if max_repairs < 0:
            raise ValueError("max_repairs must be >= 0")
        self._provider = provider
        self._max_repairs = max_repairs

    async def plan(self, normalized_task: str) -> list[ResearchStep]:
        payload = await self._provider.generate_json(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=(
                "Research task:\n"
                f"{normalized_task}\n\n"
                "Return the required JSON plan."
            ),
        )

        last_error: Exception | None = None

        for repair_index in range(self._max_repairs + 1):
            try:
                decision = ResearchPlanDecision.model_validate(payload)
                return decision.steps
            except ValidationError as exc:
                last_error = exc

            if repair_index >= self._max_repairs:
                break

            payload = await self._provider.generate_json(
                system_prompt=PLANNER_SYSTEM_PROMPT,
                user_prompt=self._build_repair_prompt(
                    task=normalized_task,
                    invalid_payload=payload,
                ),
            )

        raise ResearchDecisionValidationError(
            "LLM returned an invalid research plan after bounded repair"
        ) from last_error

    @staticmethod
    def _build_repair_prompt(
        *,
        task: str,
        invalid_payload: dict[str, Any],
    ) -> str:
        return (
            "The previous planning JSON violated the required schema.\n"
            "Do not preserve the invalid structure.\n"
            "If multiple steps were returned, MERGE their useful content into "
            "exactly one bounded research objective.\n\n"
            f"Research task:\n{task}\n\n"
            "Invalid JSON:\n"
            f"{json.dumps(invalid_payload, ensure_ascii=False, indent=2)}\n\n"
            "Return corrected JSON with exactly one item in `steps`."
        )


class LLMResearchActionSelector:
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

        normalized: dict[str, str] = {}
        for name, description in capabilities.items():
            normalized_name = name.strip()
            normalized_description = description.strip()
            if not normalized_name or not normalized_description:
                raise ValueError(
                    "capability names and descriptions must not be empty"
                )
            normalized[normalized_name] = normalized_description

        self._provider = provider
        self._capabilities = normalized
        self._max_repairs = max_repairs

    async def select_action(
        self,
        state: ResearchState,
    ) -> ResearchAction | None:
        payload = await self._provider.generate_json(
            system_prompt=ACTION_SYSTEM_PROMPT,
            user_prompt=self._build_user_prompt(state),
        )

        last_error: Exception | None = None

        for repair_index in range(self._max_repairs + 1):
            try:
                decision = ResearchActionDecision.model_validate(payload)
                self._validate_allowed_action(decision)
                return decision.action
            except (ValidationError, ResearchDecisionValidationError) as exc:
                last_error = exc

            if repair_index >= self._max_repairs:
                break

            payload = await self._provider.generate_json(
                system_prompt=ACTION_SYSTEM_PROMPT,
                user_prompt=self._build_action_repair_prompt(
                    state=state,
                    invalid_payload=payload,
                ),
            )

        raise ResearchDecisionValidationError(
            "LLM returned an invalid research action after bounded repair"
        ) from last_error

    def _validate_allowed_action(
        self,
        decision: ResearchActionDecision,
    ) -> None:
        if (
            decision.action is not None
            and decision.action.tool_name not in self._capabilities
        ):
            raise ResearchDecisionValidationError(
                "LLM selected a capability outside the allowed set"
            )

    def _build_action_repair_prompt(
        self,
        *,
        state: ResearchState,
        invalid_payload: dict[str, Any],
    ) -> str:
        return (
            "The previous action JSON violated the action contract or selected "
            "a capability outside the allowed set.\n"
            "The invalid action has NOT been executed.\n\n"
            "Invalid JSON:\n"
            f"{json.dumps(invalid_payload, ensure_ascii=False, indent=2)}\n\n"
            f"{self._build_user_prompt(state)}\n\n"
            "Return one corrected next-action JSON using only an allowed "
            "capability, or {\"action\": null}."
        )

    def _build_user_prompt(self, state: ResearchState) -> str:
        current_step_index = state.get("current_step", 0)
        plan = state.get("plan") or []
        current_objective = (
            plan[current_step_index].objective
            if current_step_index < len(plan)
            else state.get("normalized_task", state["query"])
        )

        payload = {
            "task": state.get("normalized_task", state["query"]),
            "current_objective": current_objective,
            "current_step": current_step_index,
            "step_count": state.get("step_count", 0),
            "max_steps": state.get("max_steps", 0),
            "retry_count": state.get("retry_count", 0),
            "max_retries": state.get("max_retries", 0),
            "last_action": self._action_summary(state),
            "verification": self._verification_summary(state),
            "evidence": self._evidence_summary(state),
            "last_tool_result": self._tool_result_summary(state),
            "allowed_capabilities": self._capabilities,
        }

        return (
            "Current research state JSON:\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
            + "\n\nReturn the next-action JSON."
        )

    @staticmethod
    def _action_summary(
        state: ResearchState,
    ) -> dict[str, Any] | None:
        action = state.get("last_action")
        if action is None:
            return None
        return {
            "tool_name": action.tool_name,
            "arguments": action.arguments,
            "reason": action.reason,
        }

    @staticmethod
    def _verification_summary(
        state: ResearchState,
    ) -> dict[str, Any] | None:
        verification = state.get("verification")
        if verification is None:
            return None
        return {
            "sufficient": verification.sufficient,
            "reason": verification.reason,
            "unresolved_questions": verification.unresolved_questions,
        }

    @staticmethod
    def _evidence_summary(
        state: ResearchState,
    ) -> dict[str, Any] | None:
        pack = state.get("evidence_pack")
        if pack is None:
            return None

        return {
            "count": len(pack.evidence),
            "provenance_integrity": pack.provenance_integrity,
            "incomplete": pack.incomplete,
            "issues": [
                {
                    "kind": issue.kind.value,
                    "tool_name": issue.tool_name,
                    "error_code": (
                        issue.error_code.value
                        if issue.error_code is not None
                        else None
                    ),
                    "file_path": issue.file_path,
                }
                for issue in pack.issues[:5]
            ],
            "items": [
                {
                    "file_path": item.file_path,
                    "symbol": item.symbol,
                    "line_start": item.line_start,
                    "line_end": item.line_end,
                    "snippet_preview": item.snippet[:800],
                }
                for item in pack.evidence[:3]
            ],
        }

    @staticmethod
    def _tool_result_summary(
        state: ResearchState,
    ) -> dict[str, Any] | None:
        result = state.get("last_tool_result")
        if result is None:
            return None

        return {
            "ok": result.ok,
            "error_code": (
                result.error_code.value
                if result.error_code is not None
                else None
            ),
            "truncated": result.truncated,
            "data_keys": sorted((result.data or {}).keys()),
        }
