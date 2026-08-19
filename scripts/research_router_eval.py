from __future__ import annotations

import json
from pathlib import Path

from app.research.task_router import HeuristicTaskRouter


def main() -> None:
    root = Path.cwd()
    cases = json.loads(
        (root / "evals/research/routing_cases.json").read_text(
            encoding="utf-8"
        )
    )

    router = HeuristicTaskRouter()
    rows = []

    for case in cases:
        decision = router.route(case["query"])
        route_correct = decision.route.value == case["expected_route"]
        tier_correct = (
            decision.model_tier.value == case["expected_model_tier"]
        )
        rows.append(
            {
                "case_id": case["case_id"],
                "query": case["query"],
                "expected_route": case["expected_route"],
                "actual_route": decision.route.value,
                "expected_model_tier": case["expected_model_tier"],
                "actual_model_tier": decision.model_tier.value,
                "route_correctness": route_correct,
                "model_tier_correctness": tier_correct,
                "signals": decision.signals,
            }
        )

    route_hits = sum(row["route_correctness"] for row in rows)
    tier_hits = sum(row["model_tier_correctness"] for row in rows)
    total = len(rows)

    result = {
        "route_accuracy": route_hits / total,
        "model_tier_accuracy": tier_hits / total,
        "passed": route_hits == total and tier_hits == total,
        "cases": rows,
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))
    assert result["passed"]


if __name__ == "__main__":
    main()
