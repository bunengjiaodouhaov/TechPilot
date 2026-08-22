from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import sys
from collections import Counter
from pathlib import Path
from typing import Any


if __package__ in (None, ""):
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

from scripts.document_corpus_contract import (
    load_corpus_manifest,
    validate_corpus_files,
)
from scripts.eval_contract import EvaluationContractError, sha256_file


SCRIPT_VERSION = "document-benchmark-ingest-v2"


def split_text_with_overlap(
    text: str,
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[str]:
    if max_chars < 100:
        raise ValueError("max_chars must be at least 100")
    if overlap_chars < 0:
        raise ValueError("overlap_chars must be non-negative")
    if overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be smaller than max_chars")

    remaining = text.strip()
    if not remaining:
        return []
    if len(remaining) <= max_chars:
        return [remaining]

    pieces: list[str] = []
    start = 0
    text_length = len(remaining)

    while start < text_length:
        hard_end = min(text_length, start + max_chars)
        end = hard_end

        if hard_end < text_length:
            split_at = remaining.rfind(" ", start + 1, hard_end + 1)
            if split_at > start:
                end = split_at

        piece = remaining[start:end].strip()
        if piece:
            pieces.append(piece)

        if end >= text_length:
            break

        next_start = max(0, end - overlap_chars)

        # Move to a word boundary without discarding the intended overlap.
        if next_start > 0:
            boundary = remaining.find(" ", next_start, end)
            if boundary != -1 and boundary + 1 < end:
                next_start = boundary + 1

        if next_start <= start:
            next_start = end
        start = next_start

    return pieces


def make_benchmark_chunker(
    *,
    max_chars: int,
    overlap_chars: int,
):
    # Lazy import keeps pure chunking-contract tests independent of the app
    # runtime while still using the real StructureAwareChunker implementation
    # during benchmark ingestion.
    from app.ingestion.chunker import StructureAwareChunker

    class BenchmarkOverlapChunker(StructureAwareChunker):
        def __init__(self) -> None:
            super().__init__(max_chars=max_chars)
            self.overlap_chars = overlap_chars

        def _split_text(
            self,
            text: str,
            max_chars: int,
        ) -> list[str]:
            return split_text_with_overlap(
                text,
                max_chars=max_chars,
                overlap_chars=self.overlap_chars,
            )

    if overlap_chars < 0:
        raise ValueError("overlap_chars must be non-negative")
    if overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be smaller than max_chars")
    return BenchmarkOverlapChunker()


def _content_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    if path.suffix.lower() == ".pdf":
        return "application/pdf"
    if path.suffix.lower() in {".md", ".markdown"}:
        return "text/markdown"
    if path.suffix.lower() == ".txt":
        return "text/plain"
    return "application/octet-stream"


async def ingest_corpus(
    *,
    corpus_root: Path,
    workspace_name: str,
    output_dir: Path,
    chunk_max_chars: int = 1200,
    chunk_overlap_chars: int = 0,
) -> dict[str, Any]:
    from sqlalchemy import select

    from app.api.dependencies import get_indexing_service
    from app.db.session import AsyncSessionLocal
    from app.ingestion.service import IngestionService
    from app.models.document import Document
    from app.models.document_status import DocumentStatus
    from app.models.workspace import Workspace

    if chunk_max_chars < 100:
        raise EvaluationContractError("chunk_max_chars must be >= 100")
    if not 0 <= chunk_overlap_chars < chunk_max_chars:
        raise EvaluationContractError(
            "chunk_overlap_chars must be in [0, chunk_max_chars)"
        )

    manifest_path = corpus_root / "corpus_manifest.json"
    manifest = load_corpus_manifest(manifest_path)
    validate_corpus_files(manifest_path=manifest_path, manifest=manifest)

    normalized_workspace_name = workspace_name.strip()
    if not normalized_workspace_name:
        raise EvaluationContractError("workspace_name must not be empty")

    config = {
        "chunk_max_chars": chunk_max_chars,
        "chunk_overlap_chars": chunk_overlap_chars,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = output_dir / "benchmark_chunking_config.json"

    if config_path.exists():
        existing_config = json.loads(config_path.read_text(encoding="utf-8"))
        if existing_config != config:
            raise EvaluationContractError(
                f"output directory already belongs to a different chunk config: "
                f"{existing_config} != {config}"
            )
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Workspace).where(Workspace.name == normalized_workspace_name)
        )
        workspaces = list(result.scalars())
        if len(workspaces) > 1:
            raise EvaluationContractError(
                f"multiple workspaces named {normalized_workspace_name!r}"
            )
        if workspaces:
            workspace = workspaces[0]
            workspace_created = False
        else:
            workspace = Workspace(name=normalized_workspace_name)
            session.add(workspace)
            await session.commit()
            await session.refresh(workspace)
            workspace_created = True

        service = IngestionService(
            session=session,
            chunker=make_benchmark_chunker(
                max_chars=chunk_max_chars,
                overlap_chars=chunk_overlap_chars,
            ),
            indexing_service=get_indexing_service(),
        )

        rows: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []

        for index, corpus_document in enumerate(manifest.documents, 1):
            source_path = (corpus_root / corpus_document.source_path).resolve()
            if not source_path.is_file():
                raise EvaluationContractError(
                    f"source file missing: {corpus_document.document_key}"
                )
            if sha256_file(source_path) != corpus_document.source_sha256:
                raise EvaluationContractError(
                    f"source SHA mismatch before ingestion: "
                    f"{corpus_document.document_key}"
                )

            existing_result = await session.execute(
                select(Document)
                .where(
                    Document.workspace_id == workspace.id,
                    Document.checksum == corpus_document.source_sha256,
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
            existing = list(existing_result.scalars())
            if len(existing) > 1:
                raise EvaluationContractError(
                    f"duplicate successful corpus document in benchmark workspace: "
                    f"{corpus_document.document_key}"
                )

            if existing:
                document = existing[0]
                rows.append(
                    {
                        "document_key": corpus_document.document_key,
                        "source_sha256": corpus_document.source_sha256,
                        "db_document_id": document.id,
                        "document_name": document.name,
                        "status": document.status,
                        "action": "reuse",
                    }
                )
                print(
                    f"[{index}/{len(manifest.documents)}] reuse "
                    f"{corpus_document.document_key} -> document_id={document.id}"
                )
                continue

            try:
                result = await service.ingest(
                    workspace_id=workspace.id,
                    filename=source_path.name,
                    content_type=_content_type(source_path),
                    file_bytes=source_path.read_bytes(),
                )
                rows.append(
                    {
                        "document_key": corpus_document.document_key,
                        "source_sha256": corpus_document.source_sha256,
                        "db_document_id": result.document_id,
                        "document_name": source_path.name,
                        "status": result.status,
                        "chunk_count": result.chunk_count,
                        "action": "ingest",
                    }
                )
                print(
                    f"[{index}/{len(manifest.documents)}] ingest "
                    f"{corpus_document.document_key} -> "
                    f"document_id={result.document_id} chunks={result.chunk_count}"
                )
            except Exception as exc:
                failures.append(
                    {
                        "document_key": corpus_document.document_key,
                        "source_sha256": corpus_document.source_sha256,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                print(
                    f"[{index}/{len(manifest.documents)}] FAIL "
                    f"{corpus_document.document_key}: {type(exc).__name__}: {exc}"
                )

        mapping_path = output_dir / "benchmark_document_mapping.jsonl"
        with mapping_path.open("w", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")

        failures_path = output_dir / "benchmark_ingestion_failures.jsonl"
        with failures_path.open("w", encoding="utf-8") as file:
            for row in failures:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")

        chunk_counts = [
            int(row["chunk_count"])
            for row in rows
            if row.get("chunk_count") is not None
        ]
        summary = {
            "script_version": SCRIPT_VERSION,
            "corpus_id": manifest.corpus_id,
            "corpus_version": manifest.corpus_version,
            "corpus_manifest_sha256": sha256_file(manifest_path),
            "workspace_id": workspace.id,
            "workspace_name": workspace.name,
            "workspace_created": workspace_created,
            "chunking": config,
            "requested_document_count": len(manifest.documents),
            "successful_document_count": len(rows),
            "ingested_count": sum(row["action"] == "ingest" for row in rows),
            "reused_count": sum(row["action"] == "reuse" for row in rows),
            "failure_count": len(failures),
            "complete": len(rows) == len(manifest.documents) and not failures,
            "status_counts": dict(
                sorted(Counter(row["status"] for row in rows).items())
            ),
            "total_chunks": sum(chunk_counts),
            "mapping_path": str(mapping_path),
            "failures_path": str(failures_path),
        }
        summary_path = output_dir / "benchmark_ingestion_summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest frozen benchmark corpus with a controlled chunk configuration."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {SCRIPT_VERSION}",
    )
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--workspace-name", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--chunk-max-chars", type=int, default=1200)
    parser.add_argument("--chunk-overlap-chars", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = asyncio.run(
        ingest_corpus(
            corpus_root=args.corpus_root,
            workspace_name=args.workspace_name,
            output_dir=args.output_dir,
            chunk_max_chars=args.chunk_max_chars,
            chunk_overlap_chars=args.chunk_overlap_chars,
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
