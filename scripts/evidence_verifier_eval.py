from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.answering.deepseek_evidence_verifier import DeepSeekEvidenceVerifierProvider
from app.answering.evidence_dto import (
    EvidenceItem,
    EvidenceReason,
    EvidenceState,
    EvidenceVerificationInput,
)
from app.prompts.evidence_verifier import EVIDENCE_VERIFIER_PROMPT_VERSION


@dataclass(frozen=True)
class EvidenceEvaluationCase:
    id: str
    target: str
    evidence: tuple[EvidenceItem, ...]
    expected_state: EvidenceState
    expected_reasons: tuple[EvidenceReason, ...]
    notes: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceEvaluationCase":
        evidence_raw = data.get("evidence")
        if not isinstance(evidence_raw, list):
            raise ValueError("evidence must be an array")

        evidence = tuple(
            EvidenceItem(
                source_id=str(item["source_id"]),
                text=str(item["text"]),
                source_type=str(item["source_type"]),
                source_ref=str(item["source_ref"]),
                title=(
                    str(item["title"])
                    if item.get("title") is not None
                    else None
                ),
                locator=(
                    str(item["locator"])
                    if item.get("locator") is not None
                    else None
                ),
            )
            for item in evidence_raw
        )

        case = cls(
            id=str(data["id"]).strip(),
            target=str(data["target"]).strip(),
            evidence=evidence,
            expected_state=EvidenceState(str(data["expected_state"])),
            expected_reasons=tuple(
                EvidenceReason(str(reason))
                for reason in data.get("expected_reasons", [])
            ),
            notes=str(data.get("notes", "")).strip(),
        )
        case.validate()
        return case

    def validate(self) -> None:
        if not self.id:
            raise ValueError("case id must not be empty")
        if not self.target:
            raise ValueError(f"target must not be empty: {self.id}")
        source_ids = [item.source_id.strip() for item in self.evidence]
        if any(not source_id for source_id in source_ids):
            raise ValueError(f"evidence source_id must not be empty: {self.id}")
        if len(set(source_ids)) != len(source_ids):
            raise ValueError(f"duplicate evidence source_id: {self.id}")
        if self.expected_state is EvidenceState.SUFFICIENT and self.expected_reasons:
            raise ValueError(
                f"sufficient case must not declare failure reasons: {self.id}"
            )
        if self.expected_state is not EvidenceState.SUFFICIENT and not self.expected_reasons:
            raise ValueError(
                f"non-sufficient case must declare expected_reasons: {self.id}"
            )


@dataclass(frozen=True)
class EvidenceEvaluationResult:
    case: EvidenceEvaluationCase
    actual_state: EvidenceState | None
    actual_reasons: tuple[EvidenceReason, ...]
    supporting_source_ids: tuple[str, ...]
    conflicting_source_ids: tuple[str, ...]
    explanation: str | None
    error: str | None


@dataclass(frozen=True)
class EvidenceEvaluationSummary:
    total_cases: int
    runtime_errors: int
    evaluated_cases: int
    state_correct: int
    state_accuracy: float | None
    reason_exact_matches: int
    reason_exact_match_rate: float | None
    sufficient_cases: int
    sufficient_state_correct: int
    insufficient_cases: int
    insufficient_state_correct: int
    conflicting_cases: int
    conflicting_state_correct: int


def load_cases(path: Path) -> list[EvidenceEvaluationCase]:
    if not path.is_file():
        raise FileNotFoundError(f"Evidence verifier dataset not found: {path}")

    cases: list[EvidenceEvaluationCase] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON on line {line_number}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(f"Case on line {line_number} must be an object")
        case = EvidenceEvaluationCase.from_dict(data)
        if case.id in seen_ids:
            raise ValueError(f"Duplicate case id on line {line_number}: {case.id}")
        seen_ids.add(case.id)
        cases.append(case)

    if not cases:
        raise ValueError("Evidence verifier dataset contains no cases")
    return cases


def summarize_results(
    results: list[EvidenceEvaluationResult],
) -> EvidenceEvaluationSummary:
    evaluated = [result for result in results if result.error is None]
    state_correct = sum(
        result.actual_state is result.case.expected_state
        for result in evaluated
    )
    reason_exact_matches = sum(
        set(result.actual_reasons) == set(result.case.expected_reasons)
        for result in evaluated
    )

    def expected(state: EvidenceState) -> list[EvidenceEvaluationResult]:
        return [result for result in evaluated if result.case.expected_state is state]

    sufficient = expected(EvidenceState.SUFFICIENT)
    insufficient = expected(EvidenceState.INSUFFICIENT)
    conflicting = expected(EvidenceState.CONFLICTING)

    def correct(rows: list[EvidenceEvaluationResult], state: EvidenceState) -> int:
        return sum(result.actual_state is state for result in rows)

    count = len(evaluated)
    return EvidenceEvaluationSummary(
        total_cases=len(results),
        runtime_errors=sum(result.error is not None for result in results),
        evaluated_cases=count,
        state_correct=state_correct,
        state_accuracy=(state_correct / count if count else None),
        reason_exact_matches=reason_exact_matches,
        reason_exact_match_rate=(reason_exact_matches / count if count else None),
        sufficient_cases=len(sufficient),
        sufficient_state_correct=correct(sufficient, EvidenceState.SUFFICIENT),
        insufficient_cases=len(insufficient),
        insufficient_state_correct=correct(insufficient, EvidenceState.INSUFFICIENT),
        conflicting_cases=len(conflicting),
        conflicting_state_correct=correct(conflicting, EvidenceState.CONFLICTING),
    )


async def evaluate_case(
    *,
    provider: DeepSeekEvidenceVerifierProvider,
    case: EvidenceEvaluationCase,
) -> EvidenceEvaluationResult:
    request = EvidenceVerificationInput(target=case.target, evidence=case.evidence)
    try:
        result = await provider.verify(request=request)
    except Exception as exc:
        return EvidenceEvaluationResult(
            case=case,
            actual_state=None,
            actual_reasons=(),
            supporting_source_ids=(),
            conflicting_source_ids=(),
            explanation=None,
            error=f"{type(exc).__name__}: {exc}",
        )

    return EvidenceEvaluationResult(
        case=case,
        actual_state=result.state,
        actual_reasons=result.reasons,
        supporting_source_ids=result.supporting_source_ids,
        conflicting_source_ids=result.conflicting_source_ids,
        explanation=result.explanation,
        error=None,
    )


def write_results(*, path: Path, results: list[EvidenceEvaluationResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for result in results:
            payload = {
                "case": {
                    "id": result.case.id,
                    "target": result.case.target,
                    "evidence": [item.model_dump(mode="json") for item in result.case.evidence],
                    "expected_state": result.case.expected_state.value,
                    "expected_reasons": [
                        reason.value for reason in result.case.expected_reasons
                    ],
                    "notes": result.case.notes,
                },
                "actual": {
                    "state": (
                        result.actual_state.value
                        if result.actual_state is not None
                        else None
                    ),
                    "reasons": [reason.value for reason in result.actual_reasons],
                    "supporting_source_ids": list(result.supporting_source_ids),
                    "conflicting_source_ids": list(result.conflicting_source_ids),
                    "explanation": result.explanation,
                    "error": result.error,
                },
            }
            json.dump(payload, file, ensure_ascii=False)
            file.write("\n")


def format_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.6f}"


async def run(*, dataset_path: Path, output_path: Path) -> None:
    from app.core.config import settings

    cases = load_cases(dataset_path)
    provider = DeepSeekEvidenceVerifierProvider(
        api_key=settings.deepseek_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )

    results: list[EvidenceEvaluationResult] = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case.id}: {case.target}")
        result = await evaluate_case(provider=provider, case=case)
        results.append(result)
        if result.error:
            print("  ERROR:", result.error)
        else:
            print(
                "  expected=",
                case.expected_state.value,
                "actual=",
                result.actual_state.value if result.actual_state else None,
            )

    write_results(path=output_path, results=results)
    summary = summarize_results(results)
    print()
    print("=" * 80)
    print("EVIDENCE VERIFIER EVALUATION")
    print("prompt_version:", EVIDENCE_VERIFIER_PROMPT_VERSION)
    print("dataset:", dataset_path)
    print("cases:", summary.total_cases)
    print("runtime_errors:", summary.runtime_errors)
    print("evaluated_cases:", summary.evaluated_cases)
    print("state_correct:", summary.state_correct)
    print("state_accuracy:", format_rate(summary.state_accuracy))
    print("reason_exact_matches:", summary.reason_exact_matches)
    print("reason_exact_match_rate:", format_rate(summary.reason_exact_match_rate))
    print(
        "sufficient:",
        f"{summary.sufficient_state_correct}/{summary.sufficient_cases}",
    )
    print(
        "insufficient:",
        f"{summary.insufficient_state_correct}/{summary.insufficient_cases}",
    )
    print(
        "conflicting:",
        f"{summary.conflicting_state_correct}/{summary.conflicting_cases}",
    )
    print("results:", output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate evidence sufficiency decisions against a reviewed JSONL dataset."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("eval/evidence_verifier_golden.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("eval/evidence_verifier_results.jsonl"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(run(dataset_path=args.dataset, output_path=args.output))


if __name__ == "__main__":
    main()
