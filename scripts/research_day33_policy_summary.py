from __future__ import annotations

import json

from app.research.context_metrics import (
    DecisionContextRequirement,
    evaluate_context_coverage,
)


def main() -> None:
    requirements = [
        DecisionContextRequirement(
            requirement_id="timeout-boundary",
            required_markers=[
                "asyncio.wait_for",
                "tool.timeout_seconds",
            ],
        ),
        DecisionContextRequirement(
            requirement_id="timeout-result",
            required_markers=["ToolErrorCode.TIMEOUT"],
        ),
    ]

    prefix_context = (
        "... asyncio.wait_for(... timeout=tool.timeout_seconds) ..."
    )
    focused_context = (
        "... asyncio.wait_for(... timeout=tool.timeout_seconds) "
        "... ToolErrorCode.TIMEOUT ..."
    )

    prefix = evaluate_context_coverage(
        expected_source_paths=["app/harness/tool_runtime.py"],
        actual_source_paths=["app/harness/tool_runtime.py"],
        visible_contexts=[prefix_context],
        requirements=requirements,
        completed=False,
    )
    focused = evaluate_context_coverage(
        expected_source_paths=["app/harness/tool_runtime.py"],
        actual_source_paths=["app/harness/tool_runtime.py"],
        visible_contexts=[focused_context],
        requirements=requirements,
        completed=True,
    )

    result = {
        "day33_policy": {
            "workflow": {
                "execution": "deterministic",
                "model": None,
                "agent_autonomy": "none",
            },
            "light_agent": {
                "model": "deepseek-v4-flash",
                "max_steps": 2,
                "first_action_policy": (
                    "deterministic symbol-first when one explicit symbol exists"
                ),
                "evidence_context_strategy": "query_focused",
            },
            "research_agent": {
                "model": "deepseek-v4-pro",
                "max_steps": 5,
                "first_action_policy": "dynamic",
                "evidence_context_strategy": (
                    "prefix baseline until separately evaluated"
                ),
            },
        },
        "new_eval_dimensions": [
            "source_coverage",
            "decision_context_coverage",
            "grounded_completion",
        ],
        "controlled_ab_observation": {
            "same_model": "deepseek-v4-flash",
            "same_evidence_budget_chars": 2200,
            "same_first_action": "ToolRuntime symbol-first",
            "prefix": {
                "task_success": False,
                "termination_reason": "max_steps",
                "step_count": 2,
                "llm_calls": 1,
                "measured_latency_ms": 3236.8317910004407,
            },
            "query_focused": {
                "task_success": True,
                "termination_reason": "completed",
                "step_count": 1,
                "llm_calls": 1,
                "measured_latency_ms": 1643.9379169605672,
            },
        },
        "coverage_example": {
            "prefix": prefix.model_dump(),
            "query_focused": focused.model_dump(),
        },
        "conclusion": (
            "For narrow repository tasks, routing quality depends on more than "
            "model tier. Agent autonomy and evidence-context selection are "
            "first-class execution-policy dimensions."
        ),
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
