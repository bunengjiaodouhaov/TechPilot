from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


SCRIPT_VERSION = "answer-evidence-metrics-v1"
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        1,
    ):
        if not raw.strip():
            continue
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise ValueError(
                f"line {line_number} is not an object: {path}"
            )
        rows.append(row)
    return rows


def _tokens(text: str) -> list[str]:
    return [
        match.group(0).lower()
        for match in _TOKEN_RE.finditer(text)
    ]


def _token_f1(reference: str, actual: str) -> float:
    ref = Counter(_tokens(reference))
    act = Counter(_tokens(actual))
    if not ref or not act:
        return 0.0
    overlap = sum((ref & act).values())
    precision = overlap / sum(act.values())
    recall = overlap / sum(ref.values())
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def score(
    *,
    results_path: Path,
    truth_path: Path,
) -> dict[str, Any]:
    results = _load_jsonl(results_path)
    truth = _load_jsonl(truth_path)
    truth_by_id = {
        str(row["candidate_id"]): row
        for row in truth
    }

    per_case: list[dict[str, Any]] = []
    for row in results:
        case = row["case"]
        actual = row["actual"]
        case_id = str(case["id"])
        truth_row = truth_by_id.get(case_id)
        if truth_row is None:
            continue

        error = actual.get("error")
        refused = actual.get("refused")
        citations = actual.get("citations") or []

        relevant_chunks = truth_row["relevant_chunks"]
        relevant_by_chunk_id = {
            str(item["chunk_id"]): item
            for item in relevant_chunks
        }
        relevant_ids = set(relevant_by_chunk_id)
        cited_ids = {
            str(item["chunk_id"])
            for item in citations
        }
        supported_cited = cited_ids & relevant_ids

        citation_precision = (
            len(supported_cited) / len(cited_ids)
            if cited_ids
            else None
        )
        citation_recall = (
            len(supported_cited) / len(relevant_ids)
            if relevant_ids
            else None
        )
        citation_hit = bool(supported_cited)

        covered_shingles: set[int] = set()
        for chunk_id in supported_cited:
            covered_shingles.update(
                int(index)
                for index in relevant_by_chunk_id[
                    chunk_id
                ].get("evidence_shingle_indices", [])
            )
        evidence_shingle_count = int(
            truth_row["evidence_shingle_count"]
        )
        evidence_coverage = (
            len(covered_shingles) / evidence_shingle_count
            if evidence_shingle_count
            else 0.0
        )

        expected_doc = str(
            truth_row["expected_document_name"]
        )
        document_citation_hit = any(
            str(item.get("document_name")) == expected_doc
            for item in citations
        )

        reference_answer = str(
            case.get("reference_answer") or ""
        )
        answer_text = str(
            actual.get("answer_text") or ""
        )
        lexical_f1 = (
            _token_f1(reference_answer, answer_text)
            if error is None and refused is False
            else 0.0
        )

        per_case.append(
            {
                "id": case_id,
                "category": case.get("category"),
                "runtime_error": error is not None,
                "refused": refused,
                "citation_precision": citation_precision,
                "citation_recall": citation_recall,
                "citation_hit": citation_hit,
                "document_citation_hit": document_citation_hit,
                "evidence_coverage": evidence_coverage,
                "reference_answer_token_f1": lexical_f1,
                # Lexical F1 is diagnostic only. It is not answer correctness.
                "answer_correct": None,
            }
        )

    usable = [
        row for row in per_case
        if not row["runtime_error"]
    ]
    answered = [
        row for row in usable
        if row["refused"] is False
    ]

    def _avg(
        rows: list[dict[str, Any]],
        key: str,
    ) -> float | None:
        values = [
            float(row[key])
            for row in rows
            if row.get(key) is not None
        ]
        return mean(values) if values else None

    overall = {
        "case_count": len(per_case),
        "runtime_error_count": sum(
            row["runtime_error"] for row in per_case
        ),
        "over_refusal_count": sum(
            row["refused"] is True for row in usable
        ),
        "over_refusal_rate": (
            sum(row["refused"] is True for row in usable)
            / len(usable)
            if usable
            else None
        ),
        "answered_count": len(answered),
        "citation_hit_rate": (
            sum(row["citation_hit"] for row in answered)
            / len(answered)
            if answered
            else None
        ),
        "document_citation_hit_rate": (
            sum(
                row["document_citation_hit"]
                for row in answered
            )
            / len(answered)
            if answered
            else None
        ),
        "citation_precision": _avg(
            answered,
            "citation_precision",
        ),
        "citation_recall": _avg(
            answered,
            "citation_recall",
        ),
        "evidence_coverage": _avg(
            answered,
            "evidence_coverage",
        ),
        "reference_answer_token_f1": _avg(
            answered,
            "reference_answer_token_f1",
        ),
        "answer_correctness_status": (
            "not_scored; requires independent/manual semantic review"
        ),
    }

    by_category: dict[str, Any] = {}
    categories = sorted({
        str(row["category"])
        for row in per_case
    })
    for category in categories:
        group = [
            row for row in per_case
            if str(row["category"]) == category
        ]
        group_usable = [
            row for row in group
            if not row["runtime_error"]
        ]
        group_answered = [
            row for row in group_usable
            if row["refused"] is False
        ]
        by_category[category] = {
            "case_count": len(group),
            "over_refusal_rate": (
                sum(
                    row["refused"] is True
                    for row in group_usable
                )
                / len(group_usable)
                if group_usable
                else None
            ),
            "citation_hit_rate": (
                sum(
                    row["citation_hit"]
                    for row in group_answered
                )
                / len(group_answered)
                if group_answered
                else None
            ),
            "citation_precision": _avg(
                group_answered,
                "citation_precision",
            ),
            "citation_recall": _avg(
                group_answered,
                "citation_recall",
            ),
            "evidence_coverage": _avg(
                group_answered,
                "evidence_coverage",
            ),
        }

    return {
        "script_version": SCRIPT_VERSION,
        "overall": overall,
        "by_category": by_category,
        "per_case": per_case,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = score(
        results_path=args.results,
        truth_path=args.truth,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            payload["overall"],
            ensure_ascii=False,
            indent=2,
        )
    )
    print("metrics:", args.output)


if __name__ == "__main__":
    main()
