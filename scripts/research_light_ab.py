from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.repository.read_boundary import RepositoryReadBoundary
from app.research.execution_policy import ExecutionProfile
from app.research.task_router import ExecutionRoute, ModelTier
from scripts.research_mixed_workload import run_agent_case


QUERY = "How does ToolRuntime enforce timeout handling?"
EXPECTED_PATHS = ["app/harness/tool_runtime.py"]


def profile(
    *,
    model: str,
    snippet_chars: int,
) -> ExecutionProfile:
    return ExecutionProfile(
        route=ExecutionRoute.LIGHT_AGENT,
        model_tier=ModelTier.MEDIUM,
        model_name=model,
        max_steps=2,
        max_retries=1,
        max_decision_output_tokens=800,
        max_evidence_items=3,
        evidence_snippet_characters=snippet_chars,
    )


async def main() -> None:
    boundary = RepositoryReadBoundary(Path.cwd())

    experiments = [
        ("flash-2200", "deepseek-v4-flash", 2200),
        ("flash-5000", "deepseek-v4-flash", 5000),
        ("pro-2200", "deepseek-v4-pro", 2200),
        ("pro-5000", "deepseek-v4-pro", 5000),
    ]

    rows = []
    for case_id, model, snippet_chars in experiments:
        result = await run_agent_case(
            query=QUERY,
            expected_paths=EXPECTED_PATHS,
            boundary=boundary,
            profile=profile(
                model=model,
                snippet_chars=snippet_chars,
            ),
        )
        rows.append(
            {
                "case_id": case_id,
                "model": model,
                "evidence_snippet_characters": snippet_chars,
                "task_success": result["task_success"],
                "termination_reason": result["termination_reason"],
                "evidence_coverage": result["evidence_coverage"],
                "step_count": result["step_count"],
                "llm_calls": result["llm_calls"],
                "total_tokens": result["usage"]["total_tokens"],
                "prompt_tokens": result["usage"]["prompt_tokens"],
                "completion_tokens": result["usage"]["completion_tokens"],
                "latency_ms": result["latency_ms"],
                "estimated_cost_usd": result["estimated_cost_usd"],
                "evidence_paths": result["evidence_paths"],
            }
        )

    by_id = {row["case_id"]: row for row in rows}

    flash_context_gain = (
        int(by_id["flash-5000"]["task_success"])
        - int(by_id["flash-2200"]["task_success"])
    )
    pro_context_gain = (
        int(by_id["pro-5000"]["task_success"])
        - int(by_id["pro-2200"]["task_success"])
    )
    model_gain_at_2200 = (
        int(by_id["pro-2200"]["task_success"])
        - int(by_id["flash-2200"]["task_success"])
    )
    model_gain_at_5000 = (
        int(by_id["pro-5000"]["task_success"])
        - int(by_id["flash-5000"]["task_success"])
    )

    interpretation = {
        "flash_context_gain": flash_context_gain,
        "pro_context_gain": pro_context_gain,
        "model_gain_at_2200": model_gain_at_2200,
        "model_gain_at_5000": model_gain_at_5000,
        "reading": (
            "Positive context_gain means increasing evidence context fixed a "
            "failure at the same model. Positive model_gain means upgrading "
            "the model fixed a failure at the same context budget."
        ),
    }

    print(
        json.dumps(
            {
                "query": QUERY,
                "controlled_variables": {
                    "max_steps": 2,
                    "max_retries": 1,
                    "max_decision_output_tokens": 800,
                    "max_evidence_items": 3,
                },
                "cases": rows,
                "effect_summary": interpretation,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
