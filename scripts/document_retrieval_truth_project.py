from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


if __package__ in (None, ""):
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

from scripts.document_corpus_contract import (
    load_corpus_manifest,
    validate_corpus_files,
)
from scripts.eval_contract import EvaluationContractError, sha256_file


SCRIPT_VERSION = "document-retrieval-truth-v2"
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[._:/-][A-Za-z0-9]+)*")


@dataclass(frozen=True, slots=True)
class ChunkProjectionInput:
    chunk_db_id: int
    chunk_id: str
    document_id: int
    text: str
    page_start: int | None
    page_end: int | None
    section: str | None
    chunk_index: int = 0


@dataclass(frozen=True, slots=True)
class RelevantChunk:
    chunk_db_id: int
    chunk_id: str
    relevance_grade: int
    evidence_coverage: float
    evidence_shingle_indices: tuple[int, ...]
    page_start: int | None
    page_end: int | None


def normalize_text(text: str) -> str:
    """Normalize PDF extraction artifacts without changing semantic content."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\u00ad", "")
    # pypdf commonly preserves a line break after an explicit hyphen:
    # "privacy- enhancing" -> "privacy-enhancing".
    normalized = re.sub(r"(?<=\w)-\s+(?=\w)", "-", normalized)
    return " ".join(normalized.split()).lower()


def evidence_tokens(text: str) -> list[str]:
    normalized = normalize_text(text)
    return [match.group(0) for match in _TOKEN_RE.finditer(normalized)]


def evidence_token_spans(text: str) -> list[tuple[str, int, int]]:
    normalized = normalize_text(text)
    return [
        (match.group(0), match.start(), match.end())
        for match in _TOKEN_RE.finditer(normalized)
    ]


def make_shingles(tokens: list[str], size: int) -> list[tuple[str, ...]]:
    if not tokens:
        return []
    if size <= 0:
        raise EvaluationContractError("shingle size must be positive")
    effective = min(size, len(tokens))
    return [
        tuple(tokens[index : index + effective])
        for index in range(0, len(tokens) - effective + 1)
    ]


def page_overlaps(
    *,
    expected_page: int | None,
    chunk_page_start: int | None,
    chunk_page_end: int | None,
) -> bool:
    if expected_page is None:
        return True
    if chunk_page_start is None and chunk_page_end is None:
        return True
    start = chunk_page_start if chunk_page_start is not None else chunk_page_end
    end = chunk_page_end if chunk_page_end is not None else chunk_page_start
    assert start is not None and end is not None
    return start <= expected_page <= end


def _eligible_chunks(
    *,
    expected_page: int | None,
    expected_section: str | None,
    chunks: list[ChunkProjectionInput],
) -> list[ChunkProjectionInput]:
    eligible = [
        chunk
        for chunk in chunks
        if page_overlaps(
            expected_page=expected_page,
            chunk_page_start=chunk.page_start,
            chunk_page_end=chunk.page_end,
        )
        and not (
            expected_page is None
            and expected_section
            and chunk.section
            and expected_section != chunk.section
        )
    ]
    return sorted(
        eligible,
        key=lambda chunk: (chunk.chunk_index, chunk.chunk_db_id),
    )


def _cross_chunk_exact_projection(
    *,
    normalized_quote: str,
    quote_token_spans: list[tuple[str, int, int]],
    quote_shingle_count: int,
    effective_shingle_size: int,
    eligible: list[ChunkProjectionInput],
) -> list[RelevantChunk]:
    if len(eligible) < 2:
        return []

    pieces: list[str] = []
    spans: list[tuple[ChunkProjectionInput, int, int]] = []
    cursor = 0
    for chunk in eligible:
        piece = normalize_text(chunk.text)
        if not piece:
            continue
        if pieces:
            cursor += 1  # one space inserted by the join below
        start = cursor
        end = start + len(piece)
        pieces.append(piece)
        spans.append((chunk, start, end))
        cursor = end

    joined = " ".join(pieces)
    match_start = joined.find(normalized_quote)
    if match_start < 0:
        return []
    match_end = match_start + len(normalized_quote)

    contributing = [
        (chunk, start, end)
        for chunk, start, end in spans
        if start < match_end and end > match_start
    ]
    if len(contributing) < 2:
        return []

    shingle_indices_by_chunk: dict[int, set[int]] = {
        chunk.chunk_db_id: set() for chunk, _, _ in contributing
    }
    if quote_shingle_count == 1:
        local_ranges = [(0, 0, len(normalized_quote))]
    else:
        local_ranges = []
        for shingle_index in range(quote_shingle_count):
            first = quote_token_spans[shingle_index]
            last = quote_token_spans[
                shingle_index + effective_shingle_size - 1
            ]
            local_ranges.append(
                (shingle_index, first[1], last[2])
            )

    for shingle_index, local_start, local_end in local_ranges:
        global_start = match_start + local_start
        global_end = match_start + local_end
        for chunk, chunk_start, chunk_end in contributing:
            if chunk_start < global_end and chunk_end > global_start:
                shingle_indices_by_chunk[chunk.chunk_db_id].add(
                    shingle_index
                )

    relevant: list[RelevantChunk] = []
    for chunk, _, _ in contributing:
        covered = tuple(sorted(shingle_indices_by_chunk[chunk.chunk_db_id]))
        coverage = (
            len(covered) / quote_shingle_count
            if quote_shingle_count
            else 0.0
        )
        relevant.append(
            RelevantChunk(
                chunk_db_id=chunk.chunk_db_id,
                chunk_id=chunk.chunk_id,
                relevance_grade=2 if coverage >= 0.50 else 1,
                evidence_coverage=coverage,
                evidence_shingle_indices=covered,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
            )
        )
    return relevant


def project_evidence_to_chunks(
    *,
    evidence_quote: str,
    expected_page: int | None,
    expected_section: str | None,
    chunks: list[ChunkProjectionInput],
    shingle_size: int = 5,
    partial_threshold: float = 0.30,
) -> tuple[list[RelevantChunk], float, str]:
    if not evidence_quote.strip():
        raise EvaluationContractError("evidence_quote must not be empty")
    if not 0 < partial_threshold <= 1:
        raise EvaluationContractError("partial_threshold must be in (0, 1]")

    quote_tokens = evidence_tokens(evidence_quote)
    if not quote_tokens:
        raise EvaluationContractError("evidence_quote produced no tokens")
    effective_shingle_size = max(1, min(shingle_size, len(quote_tokens)))
    quote_shingles = make_shingles(
        quote_tokens,
        effective_shingle_size,
    )
    quote_token_spans = evidence_token_spans(evidence_quote)
    normalized_quote = normalize_text(evidence_quote)
    eligible = _eligible_chunks(
        expected_page=expected_page,
        expected_section=expected_section,
        chunks=chunks,
    )

    # 1) Exact match inside one production chunk.
    all_indices = tuple(range(len(quote_shingles)))
    exact_relevant: list[RelevantChunk] = []
    for chunk in eligible:
        if normalized_quote in normalize_text(chunk.text):
            exact_relevant.append(
                RelevantChunk(
                    chunk_db_id=chunk.chunk_db_id,
                    chunk_id=chunk.chunk_id,
                    relevance_grade=3,
                    evidence_coverage=1.0,
                    evidence_shingle_indices=all_indices,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                )
            )
    if exact_relevant:
        return exact_relevant, 1.0, "exact_quote"

    # 2) Exact quote reconstructed across adjacent production chunks.
    cross_exact = _cross_chunk_exact_projection(
        normalized_quote=normalized_quote,
        quote_token_spans=quote_token_spans,
        quote_shingle_count=len(quote_shingles),
        effective_shingle_size=effective_shingle_size,
        eligible=eligible,
    )
    if cross_exact:
        covered_union: set[int] = set()
        for item in cross_exact:
            covered_union.update(item.evidence_shingle_indices)
        union_coverage = len(covered_union) / len(quote_shingles)
        if union_coverage == 1.0:
            return cross_exact, 1.0, "exact_quote_across_chunks"

    # 3) Conservative partial/split projection. The shingle size is fixed from
    # the quote length, so short quotes are comparable to longer chunk text.
    candidates: list[tuple[ChunkProjectionInput, tuple[int, ...]]] = []
    for chunk in eligible:
        chunk_tokens = evidence_tokens(chunk.text)
        chunk_shingle_set = set(
            make_shingles(chunk_tokens, effective_shingle_size)
        )
        covered_indices = tuple(
            index
            for index, shingle in enumerate(quote_shingles)
            if shingle in chunk_shingle_set
        )
        if covered_indices:
            candidates.append((chunk, covered_indices))

    if not candidates:
        return [], 0.0, "unprojected"

    # Greedily retain only chunks that add unique quote evidence. This allows a
    # small boundary fragment to contribute without making every incidental
    # same-page overlap relevant.
    uncovered = set(range(len(quote_shingles)))
    selected: list[tuple[ChunkProjectionInput, tuple[int, ...]]] = []
    remaining = list(candidates)
    while remaining:
        ranked = sorted(
            remaining,
            key=lambda item: (
                -len(set(item[1]) & uncovered),
                item[0].chunk_index,
                item[0].chunk_db_id,
            ),
        )
        chunk, covered_indices = ranked[0]
        gain = set(covered_indices) & uncovered
        if not gain:
            break
        selected.append((chunk, covered_indices))
        uncovered -= gain
        remaining = [
            item for item in remaining if item[0].chunk_db_id != chunk.chunk_db_id
        ]

    relevant: list[RelevantChunk] = []
    covered_union: set[int] = set()
    for chunk, covered_indices in selected:
        coverage = len(covered_indices) / len(quote_shingles)
        # `partial_threshold` controls whether a single weak fragment can stand
        # alone. Smaller fragments are kept only when they add unique evidence
        # to a multi-chunk projection.
        if coverage < partial_threshold and len(selected) == 1:
            continue
        grade = 2 if coverage >= 0.75 else 1
        relevant.append(
            RelevantChunk(
                chunk_db_id=chunk.chunk_db_id,
                chunk_id=chunk.chunk_id,
                relevance_grade=grade,
                evidence_coverage=coverage,
                evidence_shingle_indices=covered_indices,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
            )
        )
        covered_union.update(covered_indices)

    if not relevant:
        return [], 0.0, "unprojected"

    union_coverage = len(covered_union) / len(quote_shingles)
    relevant.sort(
        key=lambda item: (
            -item.relevance_grade,
            -item.evidence_coverage,
            item.chunk_db_id,
        )
    )
    return relevant, union_coverage, "split_or_partial_quote"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
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
    if not rows:
        raise EvaluationContractError(f"empty JSONL: {path}")
    return rows


def _snapshot_sha(rows: Iterable[tuple[Any, Any]]) -> str:
    digest = hashlib.sha256()
    identities = sorted(
        (
            int(document.id),
            str(document.checksum),
            int(chunk.id),
            str(chunk.chunk_id),
            hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
            chunk.page_start,
            chunk.page_end,
        )
        for document, chunk in rows
    )
    for identity in identities:
        digest.update(
            (json.dumps(identity, ensure_ascii=False) + "\n").encode("utf-8")
        )
    return digest.hexdigest()


async def build_truth_map(
    *,
    dataset_path: Path,
    corpus_root: Path,
    workspace_id: int,
    output_dir: Path,
    minimum_projection_coverage: float = 0.80,
) -> dict[str, Any]:
    if workspace_id <= 0:
        raise EvaluationContractError("workspace_id must be positive")
    if not 0 < minimum_projection_coverage <= 1:
        raise EvaluationContractError(
            "minimum_projection_coverage must be in (0, 1]"
        )

    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal
    from app.models.chunk import Chunk
    from app.models.document import Document
    from app.models.document_status import DocumentStatus

    cases = _load_jsonl(dataset_path)
    manifest_path = corpus_root / "corpus_manifest.json"
    manifest = load_corpus_manifest(manifest_path)
    validate_corpus_files(manifest_path=manifest_path, manifest=manifest)
    manifest_by_key = {item.document_key: item for item in manifest.documents}

    unknown_keys = sorted(
        {
            str(case.get("document_key", ""))
            for case in cases
            if str(case.get("document_key", "")) not in manifest_by_key
        }
    )
    if unknown_keys:
        raise EvaluationContractError(
            "dataset contains document_key values outside corpus: "
            + ", ".join(unknown_keys[:5])
        )

    expected_shas = {item.source_sha256 for item in manifest.documents}

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Document, Chunk)
            .join(Chunk, Chunk.document_id == Document.id)
            .where(
                Document.workspace_id == workspace_id,
                Document.checksum.in_(expected_shas),
                Document.deleted_at.is_(None),
                Document.status.in_(
                    (
                        DocumentStatus.COMPLETED.value,
                        DocumentStatus.PARTIAL.value,
                    )
                ),
            )
            .order_by(Document.id, Chunk.chunk_index)
        )
        db_rows = list(result.all())

    documents_by_checksum: dict[str, set[int]] = {}
    document_objects: dict[int, Any] = {}
    chunks_by_document_id: dict[int, list[ChunkProjectionInput]] = {}

    for document, chunk in db_rows:
        documents_by_checksum.setdefault(document.checksum, set()).add(document.id)
        document_objects[document.id] = document
        chunks_by_document_id.setdefault(document.id, []).append(
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
        )

    for corpus_document in manifest.documents:
        ids = documents_by_checksum.get(corpus_document.source_sha256, set())
        if len(ids) != 1:
            raise EvaluationContractError(
                f"expected exactly one successful DB document for "
                f"{corpus_document.document_key}, found {len(ids)}"
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    truth_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    exact_count = 0
    cross_chunk_exact_count = 0
    split_count = 0

    for index, case in enumerate(cases, 1):
        candidate_id = str(case.get("candidate_id", "")).strip()
        if not candidate_id:
            raise EvaluationContractError("dataset case missing candidate_id")
        document_key = str(case["document_key"])
        corpus_document = manifest_by_key[document_key]
        document_id = next(
            iter(documents_by_checksum[corpus_document.source_sha256])
        )
        document = document_objects[document_id]
        relevant, union_coverage, method = project_evidence_to_chunks(
            evidence_quote=str(case.get("evidence_quote", "")),
            expected_page=(
                int(case["page"]) if case.get("page") is not None else None
            ),
            expected_section=(
                str(case["section"])
                if case.get("section") is not None
                else None
            ),
            chunks=chunks_by_document_id[document_id],
        )

        if relevant and union_coverage >= minimum_projection_coverage:
            if method == "exact_quote":
                exact_count += 1
            elif method == "exact_quote_across_chunks":
                cross_chunk_exact_count += 1
            else:
                split_count += 1
            shingle_count = len(
                make_shingles(
                    evidence_tokens(str(case["evidence_quote"])),
                    5,
                )
            )
            truth_rows.append(
                {
                    "candidate_id": candidate_id,
                    "query": str(case["query"]),
                    "category": str(case["category"]),
                    "topic": str(case.get("topic", "")),
                    "document_key": document_key,
                    "expected_document_id": document_id,
                    "expected_document_name": document.name,
                    "expected_page": case.get("page"),
                    "expected_section": case.get("section"),
                    "evidence_quote": str(case["evidence_quote"]),
                    "evidence_shingle_count": shingle_count,
                    "projection_method": method,
                    "projection_union_coverage": union_coverage,
                    "relevant_chunks": [
                        asdict(item) for item in relevant
                    ],
                }
            )
        else:
            failures.append(
                {
                    "candidate_id": candidate_id,
                    "document_key": document_key,
                    "expected_document_id": document_id,
                    "expected_page": case.get("page"),
                    "projection_method": method,
                    "projection_union_coverage": union_coverage,
                    "relevant_chunk_count": len(relevant),
                }
            )

        if index % 25 == 0 or index == len(cases):
            print(
                f"[{index}/{len(cases)}] projected={len(truth_rows)} "
                f"failed={len(failures)}"
            )

    truth_path = output_dir / "document_retrieval_truth_map.jsonl"
    with truth_path.open("w", encoding="utf-8") as file:
        for row in truth_rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")

    failures_path = output_dir / "document_retrieval_truth_failures.jsonl"
    with failures_path.open("w", encoding="utf-8") as file:
        for row in failures:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = {
        "script_version": SCRIPT_VERSION,
        "dataset_path": str(dataset_path),
        "dataset_sha256": sha256_file(dataset_path),
        "corpus_manifest_sha256": sha256_file(manifest_path),
        "workspace_id": workspace_id,
        "db_corpus_snapshot_sha256": _snapshot_sha(db_rows),
        "case_count": len(cases),
        "projected_count": len(truth_rows),
        "projection_failure_count": len(failures),
        "exact_quote_projection_count": exact_count,
        "cross_chunk_exact_projection_count": cross_chunk_exact_count,
        "split_or_partial_projection_count": split_count,
        "minimum_projection_coverage": minimum_projection_coverage,
        "complete": len(truth_rows) == len(cases) and not failures,
        "truth_map_path": str(truth_path),
        "truth_map_sha256": sha256_file(truth_path),
        "failures_path": str(failures_path),
    }
    summary_path = output_dir / "document_retrieval_truth_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Project frozen evidence onto authoritative production chunks."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {SCRIPT_VERSION}",
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--workspace-id", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--minimum-projection-coverage",
        type=float,
        default=0.80,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = asyncio.run(
        build_truth_map(
            dataset_path=args.dataset,
            corpus_root=args.corpus_root,
            workspace_id=args.workspace_id,
            output_dir=args.output_dir,
            minimum_projection_coverage=args.minimum_projection_coverage,
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
