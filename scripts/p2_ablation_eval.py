from __future__ import annotations

import argparse
import json
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


CONFIG_VERSION = "p2-ablation-v1"
SCRIPT_VERSION = "day16-ablation-v3"


@dataclass(frozen=True)
class GitIdentity:
    sha: str
    dirty: bool


def _run_git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def current_git_identity() -> GitIdentity:
    sha = _run_git("rev-parse", "HEAD") or "unknown"
    status = _run_git("status", "--porcelain")
    dirty = True if status is None else bool(status)
    return GitIdentity(sha=sha, dirty=dirty)


def make_trace_id() -> str:
    return f"p2-ablation-{uuid.uuid4()}"


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def resolve_existing_path(path: Path, patterns: tuple[str, ...]) -> Path:
    if path.exists():
        return path

    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(sorted(Path().glob(pattern)))

    unique = []
    seen = set()
    for match in matches:
        resolved = str(match)
        if resolved not in seen and match.is_file():
            seen.add(resolved)
            unique.append(match)

    if len(unique) == 1:
        return unique[0]
    if not unique:
        raise FileNotFoundError(
            f"input not found: {path}; no fallback matched {patterns}"
        )
    raise RuntimeError(
        "multiple fallback inputs matched; pass the path explicitly: "
        + ", ".join(str(item) for item in unique)
    )


def looks_like_evidence_results(path: Path) -> bool:
    """Identify a reviewed Evidence Verifier result JSONL by its content.

    Day 15 assets are local-only and their filenames are not part of the
    verifier contract.  Discovery therefore uses the stable evaluation
    semantics instead of guessing another filename: a result row must retain
    both the reviewed expected state and the verifier's actual state.
    """

    if not path.is_file() or path.suffix.lower() != ".jsonl":
        return False

    try:
        rows = load_jsonl(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False

    if not rows:
        return False

    valid_states = {"sufficient", "insufficient", "conflicting"}
    for row in rows:
        expected = _expected_state(row)
        actual = _actual_state(row)
        if expected not in valid_states or actual not in valid_states:
            return False

    return True


def _evidence_candidate_priority(path: Path) -> int:
    """Rank local Day 15 result locations without trusting filenames alone."""

    lowered_parts = [part.lower() for part in path.parts]
    name = path.name.lower()
    score = 0

    # The user's current Day 15 repair directory is the strongest formal
    # location. Backups stay discoverable, but never beat a non-backup match.
    if any("day15_full_regression_fix" in part for part in lowered_parts):
        score += 100
    if any("backup" in part for part in lowered_parts):
        score -= 100

    # Filename hints only break ties after content validation.
    for token in ("reviewed", "evidence", "verifier", "eval", "result"):
        if token in name:
            score += 5

    return score


def discover_evidence_results(search_root: Path = Path(".local")) -> Path:
    """Find the formal reviewed Evidence Verifier JSONL under `.local/days/day15*`.

    Day 15 artifacts are local-only and their directory names changed during
    repair work. Discovery therefore scans every existing `day15*` directory
    recursively and validates candidates by row schema. Backups are only used
    when no stronger non-backup candidate exists.
    """

    day15_dirs = sorted(
        path
        for path in search_root.glob("day15*")
        if path.is_dir()
    )
    candidates: list[Path] = []
    for day15_dir in day15_dirs:
        candidates.extend(sorted(day15_dir.rglob("*.jsonl")))

    matches = [
        candidate
        for candidate in candidates
        if looks_like_evidence_results(candidate)
    ]

    if not matches:
        scanned = ", ".join(str(path) for path in day15_dirs) or "none"
        raise FileNotFoundError(
            "Evidence Verifier reviewed result JSONL not found by content. "
            f"scanned Day 15 directories: {scanned}"
        )

    ranked = sorted(
        matches,
        key=lambda path: (
            _evidence_candidate_priority(path),
            path.stat().st_mtime_ns,
            str(path),
        ),
        reverse=True,
    )
    return ranked[0]


def resolve_evidence_results_path(
    path: Path | None,
    *,
    search_root: Path = Path(".local"),
) -> Path:
    # An explicit existing path always wins. A missing/default path does not
    # block auto-discovery because Day 15 local directory names are not a
    # stable project contract.
    if path is not None and path.exists():
        return path
    return discover_evidence_results(search_root)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(
                f"expected JSON object at {path}:{line_number}"
            )
        rows.append(row)
    return rows


def _dig(mapping: dict[str, Any], paths: Iterable[tuple[str, ...]]) -> Any:
    for path in paths:
        current: Any = mapping
        found = True
        for key in path:
            if not isinstance(current, dict) or key not in current:
                found = False
                break
            current = current[key]
        if found:
            return current
    return None


def _normalize_state(value: Any) -> str | None:
    if value is None:
        return None
    state = str(value).strip().lower()
    return state or None


def _expected_state(row: dict[str, Any]) -> str | None:
    return _normalize_state(
        _dig(
            row,
            (
                ("expected_state",),
                ("expected", "state"),
                ("case", "expected_state"),
                ("case", "state"),
            ),
        )
    )


def _actual_state(row: dict[str, Any]) -> str | None:
    return _normalize_state(
        _dig(
            row,
            (
                ("actual_state",),
                ("actual", "state"),
                ("result", "state"),
                ("verifier", "state"),
            ),
        )
    )


def _primary_reason(row: dict[str, Any]) -> str | None:
    value = _dig(
        row,
        (
            ("expected_primary_reason",),
            ("expected_reason",),
            ("expected", "primary_reason"),
            ("case", "expected_primary_reason"),
            ("case", "expected_reason"),
        ),
    )
    if value is None:
        return None
    reason = str(value).strip().lower()
    return reason or None


def _evidence_list(row: dict[str, Any]) -> list[Any] | None:
    value = _dig(
        row,
        (
            ("evidence",),
            ("case", "evidence"),
            ("input", "evidence"),
            ("verifier_input", "evidence"),
        ),
    )
    return value if isinstance(value, list) else None


def legacy_nonempty_gate_allows(row: dict[str, Any]) -> bool:
    """Counterfactual pre-verifier gate: allow whenever evidence is non-empty.

    If an older result row does not retain the evidence array, a reviewed
    `no_evidence` primary reason is the only safe fallback for inferring an
    empty evidence set. Any other missing evidence payload is treated as
    non-empty so the baseline remains deliberately permissive.
    """

    evidence = _evidence_list(row)
    if evidence is not None:
        return bool(evidence)
    return _primary_reason(row) != "no_evidence"


def _binary_gate_metrics(
    *,
    expected: list[bool],
    predicted: list[bool],
) -> dict[str, Any]:
    if len(expected) != len(predicted):
        raise ValueError("expected/predicted length mismatch")
    if not expected:
        raise ValueError("cannot score empty gate evaluation")

    correct = sum(a == b for a, b in zip(expected, predicted, strict=True))
    unsafe_accepts = sum(
        (not should_answer) and did_answer
        for should_answer, did_answer in zip(expected, predicted, strict=True)
    )
    over_refusals = sum(
        should_answer and (not did_answer)
        for should_answer, did_answer in zip(expected, predicted, strict=True)
    )

    return {
        "cases": len(expected),
        "correct": correct,
        "accuracy": correct / len(expected),
        "unsafe_accepts": unsafe_accepts,
        "over_refusals": over_refusals,
    }


def summarize_evidence_gate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("evidence result set is empty")

    expected_states = [_expected_state(row) for row in rows]
    actual_states = [_actual_state(row) for row in rows]

    if any(state is None for state in expected_states):
        raise ValueError(
            "evidence rows must retain expected state for Day 16 ablation"
        )
    if any(state is None for state in actual_states):
        raise ValueError(
            "evidence rows must retain actual verifier state for Day 16 ablation"
        )

    expected_should_answer = [
        state == "sufficient" for state in expected_states
    ]
    legacy_should_answer = [legacy_nonempty_gate_allows(row) for row in rows]
    verifier_should_answer = [
        state == "sufficient" for state in actual_states
    ]

    state_exact = sum(
        expected == actual
        for expected, actual in zip(expected_states, actual_states, strict=True)
    )

    expected_counts: dict[str, int] = {}
    for state in expected_states:
        expected_counts[state] = expected_counts.get(state, 0) + 1

    return {
        "expected_state_counts": expected_counts,
        "state_exact": state_exact,
        "state_accuracy": state_exact / len(rows),
        "legacy_nonempty_gate": _binary_gate_metrics(
            expected=expected_should_answer,
            predicted=legacy_should_answer,
        ),
        "evidence_verifier_gate": _binary_gate_metrics(
            expected=expected_should_answer,
            predicted=verifier_should_answer,
        ),
        "cost_proxy": {
            "verifier_calls": len(rows),
            "legacy_generator_calls": sum(legacy_should_answer),
            "verified_generator_calls": sum(verifier_should_answer),
            "generator_calls_avoided_by_verifier": (
                sum(legacy_should_answer) - sum(verifier_should_answer)
            ),
        },
    }


def summarize_retrieval_ablation(summary: dict[str, Any]) -> dict[str, Any]:
    metrics = summary.get("metrics")
    latency = summary.get("latency")
    failure = summary.get("failure_analysis")

    if not isinstance(metrics, dict):
        raise ValueError("retrieval summary missing metrics")
    if not isinstance(latency, dict):
        raise ValueError("retrieval summary missing latency")
    if not isinstance(failure, dict):
        raise ValueError("retrieval summary missing failure_analysis")

    required = ("dense", "bm25", "hybrid", "hybrid_reranker")
    for name in required:
        if name not in metrics:
            raise ValueError(f"retrieval summary missing metrics.{name}")

    dense = metrics["dense"]
    bm25 = metrics["bm25"]
    hybrid = metrics["hybrid"]
    reranked = metrics["hybrid_reranker"]

    return {
        "matrix": {
            "dense": dense,
            "bm25": bm25,
            "hybrid": hybrid,
            "hybrid_reranker": reranked,
        },
        "gains": {
            "bm25_vs_dense": {
                "recall_at_k_delta": bm25["recall_at_k"] - dense["recall_at_k"],
                "mrr_at_k_delta": bm25["mrr_at_k"] - dense["mrr_at_k"],
            },
            "hybrid_vs_dense": {
                "recall_at_k_delta": hybrid["recall_at_k"] - dense["recall_at_k"],
                "mrr_at_k_delta": hybrid["mrr_at_k"] - dense["mrr_at_k"],
            },
            "reranker_vs_hybrid": {
                "recall_at_k_delta": reranked["recall_at_k"] - hybrid["recall_at_k"],
                "mrr_at_k_delta": reranked["mrr_at_k"] - hybrid["mrr_at_k"],
            },
        },
        "reranker_behavior": {
            "rescues": len(failure.get("rescues", [])),
            "regressions": len(failure.get("regressions", [])),
            "retained_hybrid_hits": failure.get("retained_hybrid_hits"),
            "rerank_candidate_miss": len(
                failure.get("rerank_candidate_miss", [])
            ),
        },
        "latency": latency,
    }


def build_report(
    *,
    retrieval_summary: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
    trace_id: str,
    config_version: str,
    git_identity: GitIdentity,
    retrieval_summary_path: Path,
    evidence_results_path: Path,
) -> dict[str, Any]:
    return {
        "trace": {
            "trace_id": trace_id,
            "git_sha": git_identity.sha,
            "git_dirty": git_identity.dirty,
            "config_version": config_version,
            "git_sha_semantics": (
                "baseline_commit_only_when_git_dirty"
                if git_identity.dirty
                else "exact_committed_tree"
            ),
        },
        "inputs": {
            "retrieval_summary": str(retrieval_summary_path),
            "evidence_results": str(evidence_results_path),
        },
        "retrieval": summarize_retrieval_ablation(retrieval_summary),
        "evidence": summarize_evidence_gate(evidence_rows),
        "source_integrity": {
            "retrieval": retrieval_summary.get("integrity"),
            "retrieval_config": retrieval_summary.get("config"),
        },
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def print_report(report: dict[str, Any]) -> None:
    trace = report["trace"]
    retrieval = report["retrieval"]
    evidence = report["evidence"]

    print("=" * 96)
    print("DAY 16 P2 ABLATION")
    print("script_version:", SCRIPT_VERSION)
    print("trace_id:", trace["trace_id"])
    print("git_sha:", trace["git_sha"])
    print("git_dirty:", trace["git_dirty"])
    print("config_version:", trace["config_version"])
    print("git_sha_semantics:", trace["git_sha_semantics"])

    print()
    print("RETRIEVAL QUALITY")
    for name, metric in retrieval["matrix"].items():
        print(
            f"{name:<18} "
            f"Recall={metric['recall_at_k']:.6f} "
            f"MRR={metric['mrr_at_k']:.6f} "
            f"MISS={metric['misses']}"
        )

    print()
    print("RERANKER GAIN")
    gain = retrieval["gains"]["reranker_vs_hybrid"]
    behavior = retrieval["reranker_behavior"]
    print(f"Recall delta: {gain['recall_at_k_delta']:+.6f}")
    print(f"MRR delta: {gain['mrr_at_k_delta']:+.6f}")
    print("rescues:", behavior["rescues"])
    print("regressions:", behavior["regressions"])
    print("retained_hybrid_hits:", behavior["retained_hybrid_hits"])

    print()
    print("EVIDENCE GATE ABLATION")
    legacy = evidence["legacy_nonempty_gate"]
    verifier = evidence["evidence_verifier_gate"]
    print(
        "legacy_nonempty_gate:",
        f"accuracy={legacy['accuracy']:.6f}",
        f"unsafe_accepts={legacy['unsafe_accepts']}",
        f"over_refusals={legacy['over_refusals']}",
    )
    print(
        "evidence_verifier_gate:",
        f"accuracy={verifier['accuracy']:.6f}",
        f"unsafe_accepts={verifier['unsafe_accepts']}",
        f"over_refusals={verifier['over_refusals']}",
    )
    print("state_accuracy:", f"{evidence['state_accuracy']:.6f}")

    print()
    print("COST PROXY")
    for key, value in evidence["cost_proxy"].items():
        print(f"{key}: {value}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the bounded Day 16 P2 ablation report from the formal "
            "retrieval summary and reviewed Evidence Verifier results."
        )
    )
    parser.add_argument(
        "--retrieval-summary",
        type=Path,
        default=Path(".local/days/day16/reranker_eval_summary.json"),
    )
    parser.add_argument(
        "--evidence-results",
        type=Path,
        default=None,
        help=(
            "Optional explicit reviewed Evidence Verifier JSONL. "
            "By default scan .local/days/day15* recursively by content."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".local/days/day16/p2_ablation_summary.json"),
    )
    parser.add_argument(
        "--config-version",
        default=CONFIG_VERSION,
    )
    parser.add_argument(
        "--trace-id",
        default=None,
        help="Optional externally supplied trace id; UUID-based value is generated by default.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    git_identity = current_git_identity()
    trace_id = args.trace_id or make_trace_id()

    retrieval_summary_path = resolve_existing_path(
        args.retrieval_summary,
        (".local/days/day16/*reranker*summary*.json",),
    )
    evidence_results_path = resolve_evidence_results_path(
        args.evidence_results,
        search_root=Path(".local"),
    )

    print("evidence_results_selected:", evidence_results_path)

    retrieval_summary = load_json(retrieval_summary_path)
    evidence_rows = load_jsonl(evidence_results_path)
    report = build_report(
        retrieval_summary=retrieval_summary,
        evidence_rows=evidence_rows,
        trace_id=trace_id,
        config_version=args.config_version,
        git_identity=git_identity,
        retrieval_summary_path=retrieval_summary_path,
        evidence_results_path=evidence_results_path,
    )
    write_json(args.output, report)
    print_report(report)
    print()
    print("ablation_report:", args.output)


if __name__ == "__main__":
    main()
