from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from app.core.config import settings
from app.jd.deepseek_extractor import DeepSeekJDExtractor, JDExtractionError
from app.jd.evaluation_schema import JDAnnotation
from app.jd.evaluator import JDEvaluator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run grounded JD structured-output evaluation."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help=(
            "JSON array of cases. Each case requires: id, text, golden. "
            "golden must match JDAnnotation."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )
    return parser.parse_args()


async def run(dataset: Path) -> dict:
    cases = json.loads(dataset.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("dataset must contain a JSON array")

    extractor = DeepSeekJDExtractor(
        api_key=settings.deepseek_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    evaluator = JDEvaluator()

    results = []
    repair_count = 0
    runtime_errors = 0

    for case in cases:
        case_id = str(case["id"])
        jd_text = str(case["text"])
        golden = JDAnnotation.model_validate(case["golden"])

        try:
            outcome = await extractor.extract_with_metadata(jd_text)
            metric = evaluator.evaluate(
                jd_text=jd_text,
                prediction=outcome.structured_jd,
                golden=golden,
            )
            repair_count += int(outcome.repair_used)
            results.append(
                {
                    "id": case_id,
                    "status": "ok",
                    "attempts": outcome.attempts,
                    "repair_used": outcome.repair_used,
                    "latency_ms": outcome.latency_ms,
                    "metrics": metric.__dict__,
                }
            )
        except (JDExtractionError, ValueError) as exc:
            runtime_errors += 1
            results.append(
                {
                    "id": case_id,
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    successful = [item for item in results if item["status"] == "ok"]

    def avg(name: str) -> float:
        if not successful:
            return 0.0
        return sum(item["metrics"][name] for item in successful) / len(successful)

    return {
        "dataset": str(dataset),
        "case_count": len(cases),
        "successful_cases": len(successful),
        "runtime_errors": runtime_errors,
        "repair_rate": repair_count / len(cases) if cases else 0.0,
        "requirement_recall": avg("requirement_recall"),
        "requirement_precision": avg("requirement_precision"),
        "hallucination_rate": avg("hallucination_rate"),
        "requirement_type_accuracy": avg("requirement_type_accuracy"),
        "evidence_span_binding_rate": avg("evidence_span_binding_rate"),
        "cases": results,
    }


def main() -> None:
    args = parse_args()
    result = asyncio.run(run(args.dataset))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
