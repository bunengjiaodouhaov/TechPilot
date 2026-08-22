from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "answer-evidence-dataset-prepare-v1"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        1,
    ):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid JSON line {line_number}: {path}"
            ) from exc
        if not isinstance(row, dict):
            raise ValueError(
                f"line {line_number} is not an object: {path}"
            )
        rows.append(row)
    if not rows:
        raise ValueError(f"no rows: {path}")
    return rows


def _stable_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _largest_remainder_quotas(
    counts: dict[str, int],
    target: int,
) -> dict[str, int]:
    total = sum(counts.values())
    if target <= 0 or target > total:
        raise ValueError("target must be in [1, total]")

    raw = {
        key: target * count / total
        for key, count in counts.items()
    }
    quotas = {
        key: int(value)
        for key, value in raw.items()
    }
    remainder = target - sum(quotas.values())

    order = sorted(
        counts,
        key=lambda key: (
            -(raw[key] - quotas[key]),
            key,
        ),
    )
    for key in order[:remainder]:
        quotas[key] += 1
    return quotas


def build_dataset(
    *,
    queries_path: Path,
    truth_path: Path,
    workspace_id: int,
    target: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    queries = _load_jsonl(queries_path)
    truth = _load_jsonl(truth_path)

    truth_by_id = {
        str(row["candidate_id"]): row
        for row in truth
    }
    if len(truth_by_id) != len(truth):
        raise ValueError("duplicate candidate_id in truth map")

    joined: list[dict[str, Any]] = []
    for row in queries:
        candidate_id = str(row["candidate_id"])
        truth_row = truth_by_id.get(candidate_id)
        if truth_row is None:
            continue
        answer_text = str(row.get("answer_text", "")).strip()
        if not answer_text:
            continue
        joined.append(
            {
                "candidate_id": candidate_id,
                "question": str(row["query"]).strip(),
                "reference_answer": answer_text,
                "category": str(row["category"]),
                "document_key": str(row["document_key"]),
                "expected_document_name": str(
                    truth_row["expected_document_name"]
                ),
                "evidence_quote": str(
                    truth_row["evidence_quote"]
                ),
                "projection_method": str(
                    truth_row["projection_method"]
                ),
            }
        )

    if target > len(joined):
        raise ValueError(
            f"target {target} exceeds joined pool {len(joined)}"
        )

    category_counts = Counter(
        row["category"] for row in joined
    )
    quotas = _largest_remainder_quotas(
        dict(category_counts),
        target,
    )

    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in joined:
        by_category[row["category"]].append(row)

    selected: list[dict[str, Any]] = []
    for category, quota in sorted(quotas.items()):
        pool = sorted(
            by_category[category],
            key=lambda row: (
                _stable_key(
                    f"{row['document_key']}|{row['candidate_id']}"
                ),
                row["candidate_id"],
            ),
        )

        # Document round-robin prevents the subset from collapsing onto a few
        # long documents while keeping deterministic selection.
        per_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in pool:
            per_doc[row["document_key"]].append(row)

        doc_order = sorted(
            per_doc,
            key=lambda doc: _stable_key(
                f"{category}|{doc}"
            ),
        )
        picked: list[dict[str, Any]] = []
        index = 0
        while len(picked) < quota:
            progressed = False
            for doc in doc_order:
                if index < len(per_doc[doc]):
                    picked.append(per_doc[doc][index])
                    progressed = True
                    if len(picked) == quota:
                        break
            if not progressed:
                break
            index += 1

        if len(picked) != quota:
            raise ValueError(
                f"unable to satisfy category quota: {category}"
            )
        selected.extend(picked)

    selected.sort(
        key=lambda row: _stable_key(row["candidate_id"])
    )

    output_rows = [
        {
            "id": row["candidate_id"],
            "question": row["question"],
            "workspace_id": workspace_id,
            "answerable": True,
            "reference_answer": row["reference_answer"],
            "expected_document_names": [
                row["expected_document_name"]
            ],
            "acceptance_criteria": [
                "Answer is semantically consistent with reference_answer.",
                "Citations must be grounded in projected source evidence.",
            ],
            "category": row["category"],
            "notes": (
                f"document_key={row['document_key']}; "
                f"projection_method={row['projection_method']}"
            ),
            "evidence_quote": row["evidence_quote"],
        }
        for row in selected
    ]

    summary = {
        "script_version": SCRIPT_VERSION,
        "target": target,
        "selected_count": len(output_rows),
        "workspace_id": workspace_id,
        "category_counts": dict(
            sorted(Counter(
                row["category"] for row in output_rows
            ).items())
        ),
        "document_count": len({
            row["notes"].split(";")[0]
            for row in output_rows
        }),
        "source_query_count": len(queries),
        "truth_count": len(truth),
    }
    return output_rows, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--workspace-id", type=int, required=True)
    parser.add_argument("--target", type=int, default=180)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {SCRIPT_VERSION}",
    )
    args = parser.parse_args()

    rows, summary = build_dataset(
        queries_path=args.queries,
        truth_path=args.truth,
        workspace_id=args.workspace_id,
        target=args.target,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(
                json.dumps(row, ensure_ascii=False) + "\n"
            )

    summary_path = args.summary or args.output.with_suffix(
        ".summary.json"
    )
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("dataset:", args.output)
    print("summary:", summary_path)


if __name__ == "__main__":
    main()
