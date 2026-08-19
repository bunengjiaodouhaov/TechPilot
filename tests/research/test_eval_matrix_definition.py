from __future__ import annotations

import json
from pathlib import Path


def test_day31_eval_matrix_has_seven_distinct_cases() -> None:
    cases = json.loads(
        Path("evals/research/eval_matrix.json").read_text(
            encoding="utf-8"
        )
    )

    assert len(cases) == 7
    assert len({case["case_id"] for case in cases}) == 7
    assert sum(case["category"] == "golden" for case in cases) == 2
    assert sum(case["category"] == "adversarial" for case in cases) == 5


def test_day31_eval_matrix_covers_core_control_outcomes() -> None:
    cases = json.loads(
        Path("evals/research/eval_matrix.json").read_text(
            encoding="utf-8"
        )
    )

    terminations = {case["expected_termination"] for case in cases}

    assert {
        "completed",
        "no_actionable_path",
        "permanent_failure",
        "retry_exhausted",
        "max_steps",
    }.issubset(terminations)
