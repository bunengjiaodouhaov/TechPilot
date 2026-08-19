from __future__ import annotations

import json
from pathlib import Path

from app.research.task_router import HeuristicTaskRouter


def main() -> None:
    cases = json.loads(
        Path("evals/research/routing_holdout_cases.json").read_text(
            encoding="utf-8"
        )
    )
    router = HeuristicTaskRouter()
    rows = []

    for case in cases:
        decision = router.route(case["query"])
        correct = decision.route.value == case["expected_route"]
        rows.append(
            {
                "case_id": case["case_id"],
                "expected_route": case["expected_route"],
                "actual_route": decision.route.value,
                "correct": correct,
                "signals": decision.signals,
            }
        )

    hits = sum(row["correct"] for row in rows)
    result = {
        "accuracy": hits / len(rows),
        "correct": hits,
        "total": len(rows),
        "note": (
            "Holdout accuracy is diagnostic. It is intentionally not required "
            "to be 100%."
        ),
        "cases": rows,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
