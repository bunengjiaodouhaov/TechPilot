from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from app.core.config import settings
from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolResult, ToolRuntime
from app.repository.call_relationship_tool import InspectCallsTool
from app.repository.call_relationships import PythonCallRelationshipService
from app.repository.code_hybrid import CodeHybridRetrievalService
from app.repository.code_index import InMemoryCodeDenseIndex, InMemoryCodeKeywordIndex
from app.repository.code_retrieval import CodeIndexingService, CodeRetrievalService
from app.repository.code_retrieval_tools import (
    SearchCodeDenseTool,
    SearchCodeHybridTool,
    SearchCodeKeywordTool,
)
from app.repository.module_structure import PythonModuleStructureService
from app.repository.module_structure_tool import InspectModulesTool
from app.repository.read_boundary import RepositoryReadBoundary
from app.repository.repo_explorer import RepoExploreRequest, RepoExplorer
from app.repository.structure_index import PythonRepositoryStructureIndex
from app.repository.tools import (
    ReadFileTool,
    SearchCodeTool,
    SearchSymbolTool,
    TreeTool,
)
from app.retrieval.embedding import SentenceTransformerEmbeddingProvider


@dataclass(frozen=True, slots=True)
class ExpectedEvidence:
    file_path: str
    symbol: str | None = None
    contains: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExpectedEvidence":
        return cls(
            file_path=str(data["file_path"]),
            symbol=(str(data["symbol"]) if data.get("symbol") is not None else None),
            contains=(str(data["contains"]) if data.get("contains") is not None else None),
        )


@dataclass(frozen=True, slots=True)
class CodeRagEvaluationCase:
    case_id: str
    category: str
    query: str
    task_intent: str
    search_mode: str
    limit: int
    expected_evidence: tuple[ExpectedEvidence, ...]
    expected_incomplete: bool | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CodeRagEvaluationCase":
        expected = tuple(
            ExpectedEvidence.from_dict(item)
            for item in data["expected_evidence"]
        )
        if not expected:
            raise ValueError("expected_evidence must not be empty")

        expected_incomplete = data.get("expected_incomplete")
        if expected_incomplete is not None and not isinstance(expected_incomplete, bool):
            raise ValueError("expected_incomplete must be bool or null")

        case = cls(
            case_id=str(data["case_id"]),
            category=str(data["category"]),
            query=str(data["query"]),
            task_intent=str(data["task_intent"]),
            search_mode=str(data["search_mode"]),
            limit=int(data.get("limit", 5)),
            expected_evidence=expected,
            expected_incomplete=expected_incomplete,
        )
        if case.limit <= 0:
            raise ValueError("limit must be greater than zero")
        return case


@dataclass(frozen=True, slots=True)
class CaseMetrics:
    raw_file_rank: int | None
    explorer_file_rank: int | None
    evidence_content_hit: bool
    exact_symbol_hit: bool | None
    provenance_integrity: bool
    incomplete: bool
    incomplete_correct: bool | None
    raw_unique_files: int
    evidence_unique_files: int
    raw_noise_files: int
    evidence_noise_files: int
    raw_file_noise_rate: float
    evidence_file_noise_rate: float
    file_compression_ratio: float


def load_cases(path: Path) -> list[CodeRagEvaluationCase]:
    if not path.is_file():
        raise FileNotFoundError(f"evaluation dataset not found: {path}")

    cases: list[CodeRagEvaluationCase] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on line {line_number}: {exc}") from exc

        case = CodeRagEvaluationCase.from_dict(payload)
        if case.case_id in seen_ids:
            raise ValueError(f"duplicate case_id: {case.case_id}")
        seen_ids.add(case.case_id)
        cases.append(case)

    if not cases:
        raise ValueError("evaluation dataset contains no cases")
    return cases


def unique_in_order(paths: list[str]) -> list[str]:
    return list(dict.fromkeys(paths))


def first_expected_file_rank(paths: list[str], expected_files: set[str]) -> int | None:
    for rank, path in enumerate(unique_in_order(paths), start=1):
        if path in expected_files:
            return rank
    return None


def noise_file_count(paths: list[str], expected_files: set[str]) -> int:
    return sum(path not in expected_files for path in unique_in_order(paths))


def file_noise_rate(paths: list[str], expected_files: set[str]) -> float:
    unique_paths = unique_in_order(paths)
    if not unique_paths:
        return 0.0
    return noise_file_count(unique_paths, expected_files) / len(unique_paths)


def file_compression_ratio(raw_paths: list[str], evidence_paths: list[str]) -> float:
    raw_count = len(unique_in_order(raw_paths))
    evidence_count = len(unique_in_order(evidence_paths))
    if raw_count == 0:
        return 0.0
    return 1.0 - (evidence_count / raw_count)


def evidence_content_hit(
    *,
    expected: tuple[ExpectedEvidence, ...],
    evidence: list[dict[str, Any]],
) -> bool:
    """Check authoritative file/content coverage without overrequiring symbol granularity."""

    for target in expected:
        matched = False
        for item in evidence:
            if item.get("file_path") != target.file_path:
                continue
            snippet = item.get("snippet")
            if target.contains is not None and (
                not isinstance(snippet, str)
                or target.contains not in snippet
            ):
                continue
            matched = True
            break
        if not matched:
            return False
    return True


def exact_symbol_hit(
    *,
    expected: tuple[ExpectedEvidence, ...],
    evidence: list[dict[str, Any]],
) -> bool | None:
    targets = [item for item in expected if item.symbol is not None]
    if not targets:
        return None

    for target in targets:
        if not any(
            item.get("file_path") == target.file_path
            and item.get("symbol") == target.symbol
            for item in evidence
        ):
            return False
    return True


def raw_candidate_paths(*, search_mode: str, result: ToolResult) -> list[str]:
    if not result.ok or result.data is None:
        return []
    if search_mode == "module":
        return [
            str(module["path"])
            for module in result.data.get("modules", [])
        ]
    return [str(match["path"]) for match in result.data.get("matches", [])]


def raw_tool_request(case: CodeRagEvaluationCase) -> tuple[str, dict[str, object]]:
    if case.search_mode == "code":
        return "search_code", {"query": case.query, "limit": case.limit}
    if case.search_mode == "symbol":
        return "search_symbol", {"query": case.query, "limit": case.limit}
    if case.search_mode == "keyword":
        return "search_code_keyword", {"query": case.query, "limit": case.limit}
    if case.search_mode == "dense":
        return "search_code_dense", {"query": case.query, "limit": case.limit}
    if case.search_mode == "hybrid":
        return "search_code_hybrid", {"query": case.query, "limit": case.limit}
    if case.search_mode == "module":
        return "inspect_modules", {"query": case.query, "limit": case.limit}
    if case.search_mode == "call":
        return "inspect_calls", {"query": case.query, "limit": case.limit}
    raise ValueError(f"unsupported search_mode: {case.search_mode}")


async def build_runtime(
    *,
    repository_root: Path,
) -> tuple[ToolRegistry, ToolRuntime, RepoExplorer, dict[str, Any]]:
    boundary = RepositoryReadBoundary(repository_root)

    structure_index = PythonRepositoryStructureIndex(boundary=boundary)
    structure_report = structure_index.rebuild()

    embedding_provider = SentenceTransformerEmbeddingProvider(
        model_name=settings.embedding_model,
        dimension=settings.embedding_dimension,
        batch_size=settings.embedding_batch_size,
    )
    keyword_index = InMemoryCodeKeywordIndex()
    dense_index = InMemoryCodeDenseIndex()
    code_indexing_service = CodeIndexingService(
        boundary=boundary,
        embedding_provider=embedding_provider,
        keyword_index=keyword_index,
        dense_index=dense_index,
    )
    code_report = await code_indexing_service.rebuild()

    retrieval_service = CodeRetrievalService(
        embedding_provider=embedding_provider,
        keyword_index=keyword_index,
        dense_index=dense_index,
    )
    hybrid_service = CodeHybridRetrievalService(
        retrieval_service=retrieval_service,
    )

    registry = ToolRegistry()
    for tool in (
        TreeTool(boundary),
        ReadFileTool(boundary),
        SearchCodeTool(boundary),
        SearchSymbolTool(boundary),
        SearchCodeKeywordTool(service=retrieval_service),
        SearchCodeDenseTool(service=retrieval_service),
        SearchCodeHybridTool(service=hybrid_service),
        InspectModulesTool(
            service=PythonModuleStructureService(index=structure_index),
        ),
        InspectCallsTool(
            service=PythonCallRelationshipService(index=structure_index),
        ),
    ):
        registry.register(tool)

    runtime = ToolRuntime()
    explorer = RepoExplorer(
        repository=repository_root.name,
        registry=registry,
        runtime=runtime,
    )
    return (
        registry,
        runtime,
        explorer,
        {
            "structure_index": {
                "python_file_count": structure_report.python_file_count,
                "module_count": structure_report.module_count,
                "call_clue_count": structure_report.call_clue_count,
                "parse_error_count": structure_report.parse_error_count,
                "read_error_count": structure_report.read_error_count,
            },
            "code_index": {
                "python_file_count": code_report.python_file_count,
                "chunk_count": code_report.chunk_count,
                "parse_error_count": code_report.parse_error_count,
                "read_error_count": code_report.read_error_count,
            },
        },
    )


async def evaluate_case(
    *,
    case: CodeRagEvaluationCase,
    registry: ToolRegistry,
    runtime: ToolRuntime,
    explorer: RepoExplorer,
) -> tuple[CaseMetrics, dict[str, Any]]:
    tool_name, arguments = raw_tool_request(case)
    raw_result = await runtime.invoke(
        tool=registry.get(tool_name),
        arguments=arguments,
        trace_metadata={"evaluation_case_id": case.case_id},
    )
    raw_paths = raw_candidate_paths(
        search_mode=case.search_mode,
        result=raw_result,
    )

    pack = await explorer.explore(
        RepoExploreRequest(
            query=case.query,
            task_intent=case.task_intent,
            search_mode=case.search_mode,
            limit=case.limit,
        ),
        trace_metadata={"evaluation_case_id": case.case_id},
    )
    evidence = [item.model_dump() for item in pack.evidence]
    evidence_paths = [item["file_path"] for item in evidence]
    expected_files = {item.file_path for item in case.expected_evidence}

    metrics = CaseMetrics(
        raw_file_rank=first_expected_file_rank(raw_paths, expected_files),
        explorer_file_rank=first_expected_file_rank(evidence_paths, expected_files),
        evidence_content_hit=evidence_content_hit(
            expected=case.expected_evidence,
            evidence=evidence,
        ),
        exact_symbol_hit=exact_symbol_hit(
            expected=case.expected_evidence,
            evidence=evidence,
        ),
        provenance_integrity=pack.provenance_integrity,
        incomplete=pack.incomplete,
        incomplete_correct=(
            None
            if case.expected_incomplete is None
            else pack.incomplete == case.expected_incomplete
        ),
        raw_unique_files=len(unique_in_order(raw_paths)),
        evidence_unique_files=len(unique_in_order(evidence_paths)),
        raw_noise_files=noise_file_count(raw_paths, expected_files),
        evidence_noise_files=noise_file_count(evidence_paths, expected_files),
        raw_file_noise_rate=file_noise_rate(raw_paths, expected_files),
        evidence_file_noise_rate=file_noise_rate(evidence_paths, expected_files),
        file_compression_ratio=file_compression_ratio(raw_paths, evidence_paths),
    )

    detail = {
        "case_id": case.case_id,
        "category": case.category,
        "query": case.query,
        "search_mode": case.search_mode,
        "limit": case.limit,
        "expected_evidence": [
            {
                "file_path": item.file_path,
                "symbol": item.symbol,
                "contains": item.contains,
            }
            for item in case.expected_evidence
        ],
        "raw_tool": tool_name,
        "raw_tool_ok": raw_result.ok,
        "raw_tool_error_code": (
            raw_result.error_code.value
            if raw_result.error_code is not None
            else None
        ),
        "raw_tool_truncated": raw_result.truncated,
        "raw_paths": unique_in_order(raw_paths),
        "evidence": evidence,
        "issues": [issue.model_dump(mode="json") for issue in pack.issues],
        "metrics": metrics.__dict__ if hasattr(metrics, "__dict__") else {
            field: getattr(metrics, field)
            for field in metrics.__dataclass_fields__
        },
    }
    return metrics, detail


def reciprocal_rank(rank: int | None) -> float:
    return 0.0 if rank is None else 1.0 / rank


def _mean_bool(values: list[bool]) -> float | None:
    return mean(values) if values else None


def summarize(
    *,
    cases: list[CodeRagEvaluationCase],
    metrics: list[CaseMetrics],
) -> dict[str, Any]:
    exact_symbol_values = [
        item.exact_symbol_hit
        for item in metrics
        if item.exact_symbol_hit is not None
    ]
    incomplete_values = [
        item.incomplete_correct
        for item in metrics
        if item.incomplete_correct is not None
    ]

    by_category: dict[str, dict[str, Any]] = {}
    for category in sorted({case.category for case in cases}):
        indexes = [
            index
            for index, case in enumerate(cases)
            if case.category == category
        ]
        items = [metrics[index] for index in indexes]
        category_exact = [
            item.exact_symbol_hit
            for item in items
            if item.exact_symbol_hit is not None
        ]
        by_category[category] = {
            "cases": len(items),
            "raw_file_hit_rate": mean(item.raw_file_rank is not None for item in items),
            "explorer_file_hit_rate": mean(
                item.explorer_file_rank is not None for item in items
            ),
            "evidence_content_hit_rate": mean(
                item.evidence_content_hit for item in items
            ),
            "exact_symbol_hit_rate": _mean_bool(
                [bool(value) for value in category_exact]
            ),
            "raw_file_mrr": mean(
                reciprocal_rank(item.raw_file_rank) for item in items
            ),
            "explorer_file_mrr": mean(
                reciprocal_rank(item.explorer_file_rank) for item in items
            ),
            "raw_noise_files_mean": mean(item.raw_noise_files for item in items),
            "evidence_noise_files_mean": mean(
                item.evidence_noise_files for item in items
            ),
            "file_compression_ratio_mean": mean(
                item.file_compression_ratio for item in items
            ),
        }

    return {
        "cases": len(metrics),
        "raw_file_hit_rate": mean(item.raw_file_rank is not None for item in metrics),
        "explorer_file_hit_rate": mean(
            item.explorer_file_rank is not None for item in metrics
        ),
        "evidence_content_hit_rate": mean(
            item.evidence_content_hit for item in metrics
        ),
        "exact_symbol_hit_rate": _mean_bool(
            [bool(value) for value in exact_symbol_values]
        ),
        "raw_file_mrr": mean(
            reciprocal_rank(item.raw_file_rank) for item in metrics
        ),
        "explorer_file_mrr": mean(
            reciprocal_rank(item.explorer_file_rank) for item in metrics
        ),
        "provenance_integrity_rate": mean(
            item.provenance_integrity for item in metrics
        ),
        "incomplete_rate": mean(item.incomplete for item in metrics),
        "incomplete_expectation_accuracy": _mean_bool(
            [bool(value) for value in incomplete_values]
        ),
        "raw_unique_files_mean": mean(item.raw_unique_files for item in metrics),
        "evidence_unique_files_mean": mean(
            item.evidence_unique_files for item in metrics
        ),
        "raw_noise_files_mean": mean(item.raw_noise_files for item in metrics),
        "evidence_noise_files_mean": mean(
            item.evidence_noise_files for item in metrics
        ),
        "raw_file_noise_rate_mean": mean(
            item.raw_file_noise_rate for item in metrics
        ),
        "evidence_file_noise_rate_mean": mean(
            item.evidence_file_noise_rate for item in metrics
        ),
        "file_compression_ratio_mean": mean(
            item.file_compression_ratio for item in metrics
        ),
        "by_category": by_category,
    }


def git_identity(repository_root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    sha = run("rev-parse", "HEAD")
    dirty = bool(run("status", "--porcelain"))
    return {
        "git_sha": sha,
        "git_dirty": dirty,
        "git_sha_semantics": (
            "baseline_commit_only_when_git_dirty"
            if dirty
            else "exact_commit"
        ),
    }


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def run_evaluation(
    *,
    repository_root: Path,
    dataset_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    cases = load_cases(dataset_path)
    registry, runtime, explorer, build_reports = await build_runtime(
        repository_root=repository_root,
    )

    metrics: list[CaseMetrics] = []
    details: list[dict[str, Any]] = []
    for case in cases:
        case_metrics, detail = await evaluate_case(
            case=case,
            registry=registry,
            runtime=runtime,
            explorer=explorer,
        )
        metrics.append(case_metrics)
        details.append(detail)

    summary = summarize(cases=cases, metrics=metrics)
    summary.update(
        {
            "dataset": str(dataset_path),
            "dataset_sha256": sha256_file(dataset_path),
            "embedding_model": settings.embedding_model,
            "embedding_dimension": settings.embedding_dimension,
            "build_reports": build_reports,
            **git_identity(repository_root),
        }
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "code_rag_eval_results.jsonl"
    with results_path.open("w", encoding="utf-8") as file:
        for detail in details:
            json.dump(detail, file, ensure_ascii=False)
            file.write("\n")

    summary_path = output_dir / "code_rag_eval_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"results: {results_path}")
    print(f"summary: {summary_path}")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate TechPilot read-only Code RAG / Repo Explorer.",
    )
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(".local/days/day26/code_rag_golden_v0_1.jsonl"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".local/days/day27"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(
        run_evaluation(
            repository_root=args.repo.resolve(),
            dataset_path=args.dataset,
            output_dir=args.output_dir,
        )
    )


if __name__ == "__main__":
    main()
