from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


if __package__ in (None, ""):
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

from scripts.eval_contract import EvaluationContractError, sha256_file


REVIEW_VERSION = "document-query-review-v1"
DECISIONS_FILENAME = "review_decisions.jsonl"
MANIFEST_FILENAME = "review_manifest.json"


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    candidate_id: str
    decision: str
    reviewer: str
    reviewed_at: str
    edited_query: str | None = None
    reason: str | None = None

    def validate(self) -> None:
        if self.decision not in {"accept", "reject", "edit"}:
            raise EvaluationContractError(
                f"invalid review decision for {self.candidate_id}: {self.decision}"
            )
        if not self.reviewer.strip():
            raise EvaluationContractError(
                f"reviewer must not be empty: {self.candidate_id}"
            )
        if self.decision == "edit":
            if self.edited_query is None or len(self.edited_query.strip()) < 12:
                raise EvaluationContractError(
                    f"edit requires edited_query: {self.candidate_id}"
                )
            if not self.edited_query.strip().endswith("?"):
                raise EvaluationContractError(
                    f"edited_query must end with ?: {self.candidate_id}"
                )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not raw.strip():
            continue
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise EvaluationContractError(
                f"expected object at {path}:{line_number}"
            )
        rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def _candidate_id(row: dict[str, Any]) -> str:
    value = str(row.get("candidate_id", "")).strip()
    if not value:
        raise EvaluationContractError("review row missing candidate_id")
    return value


def _review_priority(row: dict[str, Any]) -> str:
    curation = row.get("curation")
    if not isinstance(curation, dict):
        raise EvaluationContractError(
            f"candidate {_candidate_id(row)} missing curation metadata"
        )
    priority = str(curation.get("review_priority", "")).strip()
    if priority not in {"high", "medium", "low"}:
        raise EvaluationContractError(
            f"candidate {_candidate_id(row)} has invalid review priority"
        )
    return priority


def _flags(row: dict[str, Any]) -> tuple[str, ...]:
    curation = row.get("curation")
    if not isinstance(curation, dict):
        return ()
    values = curation.get("flags") or []
    if not isinstance(values, list):
        return ()
    return tuple(str(value) for value in values)


def _largest_remainder_quota(
    counts: dict[str, int],
    *,
    target: int,
) -> dict[str, int]:
    total = sum(counts.values())
    if target <= 0:
        raise EvaluationContractError("target must be positive")
    if target > total:
        raise EvaluationContractError(
            f"target {target} exceeds pool size {total}"
        )
    raw = {key: target * value / total for key, value in counts.items()}
    quota = {key: math.floor(value) for key, value in raw.items()}
    remaining = target - sum(quota.values())
    for key in sorted(
        counts,
        key=lambda item: (-(raw[item] - math.floor(raw[item])), item),
    ):
        if remaining == 0:
            break
        quota[key] += 1
        remaining -= 1
    return dict(sorted(quota.items()))


def load_decisions(path: Path) -> dict[str, ReviewDecision]:
    if not path.exists():
        return {}
    decisions: dict[str, ReviewDecision] = {}
    for row in _load_jsonl(path):
        decision = ReviewDecision(
            candidate_id=str(row.get("candidate_id", "")).strip(),
            decision=str(row.get("decision", "")).strip(),
            reviewer=str(row.get("reviewer", "")).strip(),
            reviewed_at=str(row.get("reviewed_at", "")).strip(),
            edited_query=(
                str(row["edited_query"]).strip()
                if row.get("edited_query") is not None
                else None
            ),
            reason=(
                str(row["reason"]).strip()
                if row.get("reason") is not None
                else None
            ),
        )
        decision.validate()
        decisions[decision.candidate_id] = decision
    return decisions


def _append_decision(path: Path, decision: ReviewDecision) -> None:
    decision.validate()
    with path.open("a", encoding="utf-8") as file:
        file.write(
            json.dumps(
                {
                    "candidate_id": decision.candidate_id,
                    "decision": decision.decision,
                    "reviewer": decision.reviewer,
                    "reviewed_at": decision.reviewed_at,
                    "edited_query": decision.edited_query,
                    "reason": decision.reason,
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def prepare_review(
    *,
    shortlist_path: Path,
    output_dir: Path,
    final_target: int,
    packet_size: int,
) -> dict[str, Any]:
    shortlist = _load_jsonl(shortlist_path)
    if final_target > len(shortlist):
        raise EvaluationContractError(
            "final target cannot exceed shortlist size"
        )
    ids = [_candidate_id(row) for row in shortlist]
    if len(set(ids)) != len(ids):
        raise EvaluationContractError("duplicate candidate_id in shortlist")

    auto_accept = [
        row for row in shortlist if _review_priority(row) == "low"
    ]
    review_cases = [
        row for row in shortlist if _review_priority(row) != "low"
    ]
    review_cases.sort(
        key=lambda row: (
            {"high": 0, "medium": 1}[_review_priority(row)],
            str(row.get("category", "")),
            str(row.get("document_key", "")),
            _candidate_id(row),
        )
    )

    category_counts = Counter(
        str(row.get("category", "")) for row in shortlist
    )
    final_category_quota = _largest_remainder_quota(
        dict(category_counts),
        target=final_target,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        output_dir / "auto_accept_candidates.jsonl",
        auto_accept,
    )
    _write_jsonl(
        output_dir / "review_cases.jsonl",
        review_cases,
    )
    decisions_path = output_dir / DECISIONS_FILENAME
    if not decisions_path.exists():
        decisions_path.write_text("", encoding="utf-8")

    packets_dir = output_dir / "review_packets"
    packets_dir.mkdir(exist_ok=True)
    for old in packets_dir.glob("packet_*.md"):
        old.unlink()

    for start in range(0, len(review_cases), packet_size):
        packet = review_cases[start : start + packet_size]
        packet_index = start // packet_size + 1
        lines = [
            f"# Document Query Review Packet {packet_index:02d}",
            "",
            "Decision rubric: accept if the query is natural, answerable from the "
            "quoted evidence, and appropriate for its category. Edit only the query "
            "wording. Reject if the case is ambiguous, benchmark-like, trivial, "
            "copy-like for semantic paraphrase, or otherwise unsuitable.",
            "",
        ]
        for offset, row in enumerate(packet, start + 1):
            curation = row.get("curation") or {}
            lines.extend(
                [
                    f"## {offset}. `{_candidate_id(row)}`",
                    "",
                    f"- Priority: `{_review_priority(row)}`",
                    f"- Category: `{row.get('category')}`",
                    f"- Document: `{row.get('document_key')}`",
                    f"- Locator: page `{row.get('page')}`, section `{row.get('section')}`",
                    f"- Flags: `{', '.join(_flags(row)) or 'none'}`",
                    f"- Query: {row.get('query')}",
                    f"- Answer: {row.get('answer_text')}",
                    f"- Evidence: {row.get('evidence_quote')}",
                    "",
                    "- Decision: `[ ] accept  [ ] reject  [ ] edit`",
                    "- Edited query:",
                    "- Reason:",
                    "",
                ]
            )
        (packets_dir / f"packet_{packet_index:02d}.md").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )

    manifest = {
        "review_version": REVIEW_VERSION,
        "shortlist_sha256": sha256_file(shortlist_path),
        "shortlist_count": len(shortlist),
        "final_target": final_target,
        "auto_accept_count": len(auto_accept),
        "review_case_count": len(review_cases),
        "review_priority_counts": dict(
            sorted(Counter(_review_priority(row) for row in shortlist).items())
        ),
        "category_counts": dict(sorted(category_counts.items())),
        "final_category_quota": final_category_quota,
        "packet_size": packet_size,
        "packet_count": math.ceil(len(review_cases) / packet_size),
    }
    (output_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def review_status(*, review_dir: Path) -> dict[str, Any]:
    manifest = json.loads(
        (review_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    review_cases = _load_jsonl(review_dir / "review_cases.jsonl")
    auto_accept = _load_jsonl(review_dir / "auto_accept_candidates.jsonl")
    decisions = load_decisions(review_dir / DECISIONS_FILENAME)
    review_ids = {_candidate_id(row) for row in review_cases}
    unknown = sorted(set(decisions) - review_ids)
    if unknown:
        raise EvaluationContractError(
            "decisions contain candidates outside review queue: "
            + ", ".join(unknown[:5])
        )

    accepted_ids = {
        candidate_id
        for candidate_id, decision in decisions.items()
        if decision.decision in {"accept", "edit"}
    }
    rejected_ids = {
        candidate_id
        for candidate_id, decision in decisions.items()
        if decision.decision == "reject"
    }
    pending = review_ids - set(decisions)

    accepted_rows = list(auto_accept) + [
        row for row in review_cases if _candidate_id(row) in accepted_ids
    ]
    accepted_categories = Counter(
        str(row.get("category", "")) for row in accepted_rows
    )
    quota = manifest["final_category_quota"]
    deficits = {
        category: max(0, int(target) - accepted_categories[category])
        for category, target in quota.items()
    }

    final_target = int(manifest["final_target"])
    status = {
        "review_version": REVIEW_VERSION,
        "final_target": final_target,
        "auto_accept_count": len(auto_accept),
        "review_decision_count": len(decisions),
        "accepted_review_count": len(accepted_ids),
        "rejected_review_count": len(rejected_ids),
        "pending_review_count": len(pending),
        "accepted_total_count": len(accepted_rows),
        "needed_for_final_target": max(
            0, final_target - len(accepted_rows)
        ),
        "accepted_category_counts": dict(
            sorted(accepted_categories.items())
        ),
        "category_deficits": dict(sorted(deficits.items())),
        "target_reached": len(accepted_rows) >= final_target,
        "category_quota_reached": all(value == 0 for value in deficits.values()),
        "ready_to_freeze": (
            len(accepted_rows) >= final_target
            and all(value == 0 for value in deficits.values())
        ),
    }
    (review_dir / "review_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return status


def _display_case(row: dict[str, Any], index: int, total: int) -> None:
    curation = row.get("curation") or {}
    print()
    print("=" * 88)
    print(f"[{index}/{total}] {_candidate_id(row)}")
    print(
        f"priority={_review_priority(row)}  "
        f"category={row.get('category')}  "
        f"document={row.get('document_key')}  page={row.get('page')}"
    )
    print(f"flags={', '.join(_flags(row)) or 'none'}")
    print("-" * 88)
    print("QUERY:")
    print(row.get("query"))
    print()
    print("ANSWER:")
    print(row.get("answer_text"))
    print()
    print("EVIDENCE:")
    print(row.get("evidence_quote"))
    print("=" * 88)


def interactive_review(
    *,
    review_dir: Path,
    reviewer: str,
    limit: int | None,
) -> dict[str, Any]:
    review_cases = _load_jsonl(review_dir / "review_cases.jsonl")
    decisions_path = review_dir / DECISIONS_FILENAME
    decisions = load_decisions(decisions_path)
    pending = [
        row for row in review_cases
        if _candidate_id(row) not in decisions
    ]
    if limit is not None:
        pending = pending[:limit]

    for index, row in enumerate(pending, 1):
        _display_case(row, index, len(pending))
        while True:
            choice = input(
                "[a]ccept [r]eject [e]dit-query [s]kip [q]uit > "
            ).strip().lower()
            if choice in {"a", "r", "e", "s", "q"}:
                break
        if choice == "q":
            break
        if choice == "s":
            continue

        reason: str | None = None
        edited_query: str | None = None
        if choice == "e":
            edited_query = input("Edited query > ").strip()
            reason = input("Reason (optional) > ").strip() or None
            decision_value = "edit"
        elif choice == "r":
            reason = input("Reject reason > ").strip() or None
            decision_value = "reject"
        else:
            decision_value = "accept"

        decision = ReviewDecision(
            candidate_id=_candidate_id(row),
            decision=decision_value,
            reviewer=reviewer,
            reviewed_at=datetime.now(timezone.utc).isoformat(),
            edited_query=edited_query,
            reason=reason,
        )
        _append_decision(decisions_path, decision)
        decisions[decision.candidate_id] = decision

    return review_status(review_dir=review_dir)


def _final_selection_key(row: dict[str, Any]) -> tuple[Any, ...]:
    curation = row.get("curation") or {}
    score = int(curation.get("quality_score", 0))
    primary = 0 if row.get("variant") == "primary" else 1
    repair_count = int(row.get("repair_count", 0))
    stable = hashlib.sha256(
        _candidate_id(row).encode("utf-8")
    ).hexdigest()
    return (-score, primary, repair_count, stable)


def freeze_accepted(
    *,
    review_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    manifest = json.loads(
        (review_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    final_target = int(manifest["final_target"])
    quota = {
        str(key): int(value)
        for key, value in manifest["final_category_quota"].items()
    }
    auto_accept = _load_jsonl(review_dir / "auto_accept_candidates.jsonl")
    review_cases = _load_jsonl(review_dir / "review_cases.jsonl")
    decisions = load_decisions(review_dir / DECISIONS_FILENAME)

    accepted: list[dict[str, Any]] = []
    for row in auto_accept:
        item = dict(row)
        item["review"] = {
            "status": "deterministic_validated",
            "decision": "auto_accept_low_risk",
        }
        accepted.append(item)

    for row in review_cases:
        candidate_id = _candidate_id(row)
        decision = decisions.get(candidate_id)
        if decision is None or decision.decision == "reject":
            continue
        item = dict(row)
        if decision.decision == "edit":
            item["query"] = decision.edited_query
        item["review"] = {
            "status": "human_reviewed",
            "decision": decision.decision,
            "reviewer": decision.reviewer,
            "reviewed_at": decision.reviewed_at,
            "reason": decision.reason,
        }
        accepted.append(item)

    if len(accepted) < final_target:
        raise EvaluationContractError(
            f"accepted pool {len(accepted)} is below final target {final_target}"
        )

    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in accepted:
        by_category[str(row.get("category", ""))].append(row)
    for category in by_category:
        by_category[category].sort(key=_final_selection_key)

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    # First satisfy category quotas with document round-robin inside each category.
    for category, target in sorted(quota.items()):
        pool = by_category.get(category, [])
        if len(pool) < target:
            raise EvaluationContractError(
                f"accepted category {category} has {len(pool)} but requires {target}"
            )
        by_document: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in pool:
            by_document[str(row.get("document_key", ""))].append(row)
        for document in by_document:
            by_document[document].sort(key=_final_selection_key)

        documents = sorted(by_document)
        category_selected = 0
        while category_selected < target:
            progressed = False
            for document in documents:
                choice = next(
                    (
                        row
                        for row in by_document[document]
                        if _candidate_id(row) not in selected_ids
                    ),
                    None,
                )
                if choice is None:
                    continue
                selected.append(choice)
                selected_ids.add(_candidate_id(choice))
                category_selected += 1
                progressed = True
                if category_selected == target:
                    break
            if not progressed:
                raise EvaluationContractError(
                    f"unable to satisfy category quota for {category}"
                )

    if len(selected) != final_target:
        raise AssertionError("final selection did not reach target")

    _write_jsonl(output_path, selected)
    summary = {
        "review_version": REVIEW_VERSION,
        "final_target": final_target,
        "accepted_pool_count": len(accepted),
        "selected_count": len(selected),
        "selected_category_counts": dict(
            sorted(Counter(str(row.get("category", "")) for row in selected).items())
        ),
        "selected_document_counts": dict(
            sorted(Counter(str(row.get("document_key", "")) for row in selected).items())
        ),
        "human_reviewed_count": sum(
            (row.get("review") or {}).get("status") == "human_reviewed"
            for row in selected
        ),
        "deterministic_validated_count": sum(
            (row.get("review") or {}).get("status") == "deterministic_validated"
            for row in selected
        ),
        "output_path": str(output_path),
        "output_sha256": sha256_file(output_path),
    }
    summary_path = output_path.with_suffix(".summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare, review, and freeze Document RAG query shortlist."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {REVIEW_VERSION}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--shortlist", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--final-target", type=int, default=400)
    prepare.add_argument("--packet-size", type=int, default=20)

    review = subparsers.add_parser("review")
    review.add_argument("--review-dir", type=Path, required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--limit", type=int)

    status = subparsers.add_parser("status")
    status.add_argument("--review-dir", type=Path, required=True)

    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--review-dir", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "prepare":
        result = prepare_review(
            shortlist_path=args.shortlist,
            output_dir=args.output_dir,
            final_target=args.final_target,
            packet_size=args.packet_size,
        )
    elif args.command == "review":
        result = interactive_review(
            review_dir=args.review_dir,
            reviewer=args.reviewer,
            limit=args.limit,
        )
    elif args.command == "status":
        result = review_status(review_dir=args.review_dir)
    elif args.command == "freeze":
        result = freeze_accepted(
            review_dir=args.review_dir,
            output_path=args.output,
        )
    else:
        raise AssertionError(args.command)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
