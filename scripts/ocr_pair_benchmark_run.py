from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import statistics
import time
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "ocr-paired-benchmark-run-v1"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        1,
    ):
        if not raw.strip():
            continue
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise ValueError(f"expected object at {path}:{line_number}")
        rows.append(row)
    if not rows:
        raise ValueError(f"empty JSONL: {path}")
    return rows


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def latency_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {
            "mean": 0.0,
            "p50": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "max": 0.0,
        }
    return {
        "mean": statistics.fmean(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values),
    }


async def ensure_workspace(
    *,
    session,
    name: str,
):
    from sqlalchemy import select
    from app.models.workspace import Workspace

    result = await session.execute(
        select(Workspace).where(Workspace.name == name)
    )
    rows = list(result.scalars())
    if len(rows) > 1:
        raise RuntimeError(f"multiple workspaces named {name!r}")
    if rows:
        return rows[0], False

    workspace = Workspace(name=name)
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace, True


async def ingest_mode(
    *,
    mode: str,
    corpus_dir: Path,
    workspace_name: str,
) -> dict[str, Any]:
    from hashlib import sha256
    from sqlalchemy import select

    from app.api.dependencies import get_indexing_service
    from app.db.session import AsyncSessionLocal
    from app.ingestion.service import IngestionService
    from app.models.document import Document
    from app.models.document_status import DocumentStatus

    files = sorted(corpus_dir.glob("*.pdf"))
    if not files:
        raise RuntimeError(f"no PDFs in {corpus_dir}")

    async with AsyncSessionLocal() as session:
        workspace, created = await ensure_workspace(
            session=session,
            name=workspace_name,
        )

        expected_checksums = {
            sha256(path.read_bytes()).hexdigest()
            for path in files
        }

        existing_result = await session.execute(
            select(Document).where(
                Document.workspace_id == workspace.id,
                Document.deleted_at.is_(None),
            )
        )
        all_existing = list(existing_result.scalars())
        foreign = [
            document
            for document in all_existing
            if document.checksum not in expected_checksums
            and document.status
            in (
                DocumentStatus.COMPLETED.value,
                DocumentStatus.PARTIAL.value,
            )
        ]
        if foreign:
            raise RuntimeError(
                f"workspace {workspace_name!r} contains unrelated active "
                f"documents; use a new workspace name"
            )

        service = IngestionService(
            session=session,
            indexing_service=get_indexing_service(),
        )

        rows: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        latencies: list[float] = []

        for index, path in enumerate(files, 1):
            checksum = sha256(path.read_bytes()).hexdigest()
            result = await session.execute(
                select(Document)
                .where(
                    Document.workspace_id == workspace.id,
                    Document.checksum == checksum,
                    Document.deleted_at.is_(None),
                    Document.status.in_(
                        (
                            DocumentStatus.COMPLETED.value,
                            DocumentStatus.PARTIAL.value,
                        )
                    ),
                )
                .order_by(Document.id)
            )
            existing = list(result.scalars())

            if len(existing) > 1:
                raise RuntimeError(
                    f"duplicate active document checksum in {workspace_name}: "
                    f"{path.name}"
                )

            if existing:
                rows.append(
                    {
                        "filename": path.name,
                        "document_id": existing[0].id,
                        "status": existing[0].status,
                        "action": "reuse",
                        "latency_ms": 0.0,
                    }
                )
                print(
                    f"[{mode} {index}/{len(files)}] reuse {path.name}"
                )
                continue

            started = time.perf_counter()
            try:
                result = await service.ingest(
                    workspace_id=workspace.id,
                    filename=path.name,
                    content_type=(
                        mimetypes.guess_type(path.name)[0]
                        or "application/pdf"
                    ),
                    file_bytes=path.read_bytes(),
                )
                elapsed = (time.perf_counter() - started) * 1000.0
                latencies.append(elapsed)
                rows.append(
                    {
                        "filename": path.name,
                        "document_id": result.document_id,
                        "status": result.status,
                        "chunk_count": result.chunk_count,
                        "action": "ingest",
                        "latency_ms": elapsed,
                    }
                )
                print(
                    f"[{mode} {index}/{len(files)}] ingest {path.name} "
                    f"chunks={result.chunk_count} {elapsed:.1f}ms"
                )
            except Exception as exc:
                elapsed = (time.perf_counter() - started) * 1000.0
                latencies.append(elapsed)
                failures.append(
                    {
                        "filename": path.name,
                        "error": f"{type(exc).__name__}: {exc}",
                        "latency_ms": elapsed,
                    }
                )
                print(
                    f"[{mode} {index}/{len(files)}] FAIL {path.name}: "
                    f"{type(exc).__name__}: {exc}"
                )

        return {
            "mode": mode,
            "workspace_id": workspace.id,
            "workspace_name": workspace.name,
            "workspace_created": created,
            "requested_document_count": len(files),
            "successful_document_count": len(rows),
            "failure_count": len(failures),
            "complete": len(rows) == len(files) and not failures,
            "ingestion_success_rate": len(rows) / len(files),
            "ingestion_latency_ms": latency_summary(latencies),
            "rows": rows,
            "failures": failures,
        }


async def project_and_run_mode(
    *,
    mode: str,
    workspace_id: int,
    cases: list[dict[str, Any]],
    top_k: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from sqlalchemy import select

    from app.api.dependencies import get_answer_retrieval_service
    from app.db.session import AsyncSessionLocal
    from app.models.chunk import Chunk
    from app.models.document import Document
    from scripts.document_retrieval_metrics import score_case
    from scripts.document_retrieval_truth_project import (
        ChunkProjectionInput,
        evidence_tokens,
        make_shingles,
        project_evidence_to_chunks,
    )

    case_rows: list[dict[str, Any]] = []
    latencies: list[float] = []

    async with AsyncSessionLocal() as session:
        retrieval = get_answer_retrieval_service(session=session)

        result = await session.execute(
            select(Document).where(
                Document.workspace_id == workspace_id,
                Document.deleted_at.is_(None),
            )
        )
        documents = list(result.scalars())
        document_by_name = {
            document.name: document
            for document in documents
        }

        for index, case in enumerate(cases, 1):
            expected_name = str(
                case[
                    "native_document_name"
                    if mode == "native"
                    else "scanned_document_name"
                ]
            )
            document = document_by_name.get(expected_name)
            if document is None:
                case_rows.append(
                    {
                        "candidate_id": case["candidate_id"],
                        "mode": mode,
                        "projection_error": (
                            f"expected document missing: {expected_name}"
                        ),
                    }
                )
                continue

            chunk_result = await session.execute(
                select(Chunk)
                .where(Chunk.document_id == document.id)
                .order_by(Chunk.chunk_index)
            )
            chunks = list(chunk_result.scalars())
            projection_inputs = [
                ChunkProjectionInput(
                    chunk_db_id=chunk.id,
                    chunk_id=chunk.chunk_id,
                    document_id=document.id,
                    text=chunk.text,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    section=chunk.section,
                    chunk_index=chunk.chunk_index,
                )
                for chunk in chunks
            ]

            relevant, union_coverage, projection_method = (
                project_evidence_to_chunks(
                    evidence_quote=str(case["evidence_quote"]),
                    expected_page=1,
                    expected_section=None,
                    chunks=projection_inputs,
                )
            )

            if not relevant or union_coverage < 0.80:
                case_rows.append(
                    {
                        "candidate_id": case["candidate_id"],
                        "mode": mode,
                        "projection_error": (
                            f"projection={projection_method} "
                            f"coverage={union_coverage:.4f}"
                        ),
                    }
                )
                continue

            shingle_count = len(
                make_shingles(
                    evidence_tokens(str(case["evidence_quote"])),
                    5,
                )
            )
            truth = {
                "candidate_id": case["candidate_id"],
                "category": case["category"],
                "document_key": case["pair_id"],
                "expected_document_id": document.id,
                "expected_document_name": document.name,
                "expected_page": 1,
                "evidence_shingle_count": shingle_count,
                "relevant_chunks": [
                    {
                        "chunk_db_id": item.chunk_db_id,
                        "chunk_id": item.chunk_id,
                        "relevance_grade": item.relevance_grade,
                        "evidence_coverage": item.evidence_coverage,
                        "evidence_shingle_indices": list(
                            item.evidence_shingle_indices
                        ),
                        "page_start": item.page_start,
                        "page_end": item.page_end,
                    }
                    for item in relevant
                ],
            }

            started = time.perf_counter()
            hits = await retrieval.search(
                query=str(case["query"]),
                workspace_id=workspace_id,
                limit=top_k,
            )
            elapsed = (time.perf_counter() - started) * 1000.0
            latencies.append(elapsed)

            run = {
                "candidate_id": case["candidate_id"],
                "variant": f"{mode}_final_retrieval",
                "latency_ms": elapsed,
                "hits": [
                    {
                        "chunk_db_id": int(hit.point_id),
                        "chunk_id": hit.payload.chunk_id,
                        "document_id": int(hit.payload.document_id),
                        "document_name": hit.payload.document_name,
                        "score": float(hit.score),
                        "page_start": hit.payload.page_start,
                        "page_end": hit.payload.page_end,
                    }
                    for hit in hits
                ],
            }

            scored = score_case(
                truth=truth,
                run=run,
                ks=(top_k,),
            )

            relevant_ids = {
                item.chunk_db_id
                for item in relevant
            }
            relevant_hits = [
                hit
                for hit in run["hits"]
                if hit["chunk_db_id"] in relevant_ids
            ]
            page_hit = any(
                (
                    (hit["page_start"] is None or hit["page_start"] <= 1)
                    and (hit["page_end"] is None or hit["page_end"] >= 1)
                )
                for hit in relevant_hits
            )

            case_rows.append(
                {
                    "candidate_id": case["candidate_id"],
                    "pair_id": case["pair_id"],
                    "mode": mode,
                    "category": case["category"],
                    "projection_method": projection_method,
                    "projection_union_coverage": union_coverage,
                    "page_hit_at_k": float(page_hit),
                    "latency_ms": elapsed,
                    "metrics": scored["metrics"],
                    "hits": run["hits"],
                }
            )

            if index % 10 == 0 or index == len(cases):
                print(
                    f"[{mode} {index}/{len(cases)}] "
                    f"projected={sum('metrics' in row for row in case_rows)}"
                )

    scored_rows = [
        row for row in case_rows if "metrics" in row
    ]
    projection_failures = [
        row for row in case_rows if "metrics" not in row
    ]

    if not scored_rows:
        raise RuntimeError(f"no scorable {mode} cases")

    metric_names = (
        f"document_hit_at_{top_k}",
        f"evidence_hit_at_{top_k}",
        f"recall_at_{top_k}",
        f"mrr_at_{top_k}",
        f"ndcg_at_{top_k}",
        f"evidence_coverage_at_{top_k}",
    )
    aggregate = {
        name: statistics.fmean(
            row["metrics"][name]
            for row in scored_rows
        )
        for name in metric_names
    }

    evidence_hit_count = sum(
        row["metrics"][f"evidence_hit_at_{top_k}"] > 0
        for row in scored_rows
    )
    page_hit_count = sum(
        row["page_hit_at_k"] > 0
        for row in scored_rows
    )
    aggregate["page_accuracy_given_evidence_hit"] = (
        page_hit_count / evidence_hit_count
        if evidence_hit_count
        else 0.0
    )
    aggregate["retrieval_latency_ms"] = latency_summary(latencies)

    summary = {
        "mode": mode,
        "case_count": len(cases),
        "scored_case_count": len(scored_rows),
        "projection_failure_count": len(projection_failures),
        "metrics": aggregate,
    }
    return summary, case_rows


async def run(args: argparse.Namespace) -> dict[str, Any]:
    pair_root = args.pair_root
    cases = load_jsonl(pair_root / "ocr_cases.jsonl")

    native_ingestion = await ingest_mode(
        mode="native",
        corpus_dir=pair_root / "native",
        workspace_name=args.native_workspace_name,
    )
    scanned_ingestion = await ingest_mode(
        mode="scanned",
        corpus_dir=pair_root / "scanned",
        workspace_name=args.scanned_workspace_name,
    )

    if not native_ingestion["complete"]:
        raise RuntimeError("native ingestion incomplete")
    if not scanned_ingestion["complete"]:
        raise RuntimeError("scanned ingestion incomplete")

    native_summary, native_cases = await project_and_run_mode(
        mode="native",
        workspace_id=int(native_ingestion["workspace_id"]),
        cases=cases,
        top_k=args.top_k,
    )
    scanned_summary, scanned_cases = await project_and_run_mode(
        mode="scanned",
        workspace_id=int(scanned_ingestion["workspace_id"]),
        cases=cases,
        top_k=args.top_k,
    )

    k = args.top_k
    native_metrics = native_summary["metrics"]
    scanned_metrics = scanned_summary["metrics"]

    def degradation(metric: str) -> dict[str, float | None]:
        native_value = float(native_metrics[metric])
        scanned_value = float(scanned_metrics[metric])
        drop = native_value - scanned_value
        return {
            "native": native_value,
            "scanned": scanned_value,
            "absolute_drop": drop,
            "relative_drop": (
                drop / native_value
                if native_value
                else None
            ),
        }

    summary = {
        "script_version": SCRIPT_VERSION,
        "top_k": k,
        "case_count": len(cases),
        "native_ingestion": {
            key: value
            for key, value in native_ingestion.items()
            if key not in {"rows", "failures"}
        },
        "scanned_ingestion": {
            key: value
            for key, value in scanned_ingestion.items()
            if key not in {"rows", "failures"}
        },
        "native_retrieval": native_summary,
        "scanned_retrieval": scanned_summary,
        "degradation": {
            "recall_at_k": degradation(f"recall_at_{k}"),
            "evidence_hit_at_k": degradation(
                f"evidence_hit_at_{k}"
            ),
            "evidence_coverage_at_k": degradation(
                f"evidence_coverage_at_{k}"
            ),
            "mrr_at_k": degradation(f"mrr_at_{k}"),
            "ndcg_at_k": degradation(f"ndcg_at_{k}"),
        },
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "ocr_benchmark_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    for name, rows in (
        ("native_cases.jsonl", native_cases),
        ("scanned_cases.jsonl", scanned_cases),
    ):
        with (args.output_dir / name).open(
            "w",
            encoding="utf-8",
        ) as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")

    (args.output_dir / "ingestion_detail.json").write_text(
        json.dumps(
            {
                "native": native_ingestion,
                "scanned": scanned_ingestion,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--native-workspace-name",
        default="eval-ocr-native-g2-v1",
    )
    parser.add_argument(
        "--scanned-workspace-name",
        default="eval-ocr-scanned-g2-v1",
    )
    parser.add_argument("--top-k", type=int, default=5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = asyncio.run(run(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
