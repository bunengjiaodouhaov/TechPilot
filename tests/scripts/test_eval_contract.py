from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.eval_contract import (
    EvaluationContractError,
    EvaluationSplit,
    ReviewStatus,
    SourceOrigin,
    build_dataset_manifest,
    load_jsonl_objects,
    validate_case_metadata,
)


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def valid_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "case_id": "doc-heldout-0001",
        "dataset_version": "document-rag-backfill-v1",
        "split": "heldout",
        "category": "semantic_paraphrase",
        "source_origin": "real",
        "review_status": "human_reviewed",
        "notes": "",
    }
    row.update(overrides)
    return row


def test_load_and_validate_metadata(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    write_jsonl(
        dataset,
        [
            valid_row(),
            valid_row(
                case_id="doc-dev-0002",
                split="dev",
                review_status="machine_validated",
                source_origin="derived",
            ),
        ],
    )

    rows = load_jsonl_objects(dataset)
    metadata = validate_case_metadata(rows)

    assert [item.case_id for item in metadata] == [
        "doc-heldout-0001",
        "doc-dev-0002",
    ]
    assert metadata[0].split is EvaluationSplit.HELDOUT
    assert metadata[0].review_status is ReviewStatus.HUMAN_REVIEWED
    assert metadata[1].source_origin is SourceOrigin.DERIVED


def test_heldout_rejects_unreviewed_machine_validation() -> None:
    with pytest.raises(EvaluationContractError, match="heldout case"):
        validate_case_metadata(
            [valid_row(review_status="machine_validated")]
        )


def test_heldout_accepts_independent_deterministic_validation() -> None:
    metadata = validate_case_metadata(
        [valid_row(review_status="deterministic_validated", source_origin="derived")]
    )
    assert metadata[0].review_status is ReviewStatus.DETERMINISTIC_VALIDATED


def test_synthetic_failure_is_not_normal_heldout() -> None:
    with pytest.raises(EvaluationContractError, match="synthetic_failure"):
        validate_case_metadata(
            [
                valid_row(
                    source_origin="synthetic_failure",
                    split="heldout",
                )
            ]
        )


def test_duplicate_case_id_fails() -> None:
    with pytest.raises(EvaluationContractError, match="duplicate case_id"):
        validate_case_metadata([valid_row(), valid_row()])


def test_mixed_dataset_versions_fail() -> None:
    with pytest.raises(EvaluationContractError, match="exactly one dataset_version"):
        validate_case_metadata(
            [
                valid_row(),
                valid_row(
                    case_id="doc-heldout-0002",
                    dataset_version="document-rag-backfill-v2",
                ),
            ]
        )


def test_manifest_records_sha_and_counts(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    write_jsonl(
        dataset,
        [
            valid_row(),
            valid_row(
                case_id="doc-dev-0002",
                split="dev",
                category="direct_fact",
                source_origin="derived",
                review_status="machine_validated",
            ),
        ],
    )

    manifest = build_dataset_manifest(
        dataset_id="document-rag-backfill",
        task="document_retrieval",
        dataset_path=dataset,
        corpus_manifest_sha256="corpus-sha",
    )

    assert manifest.case_count == 2
    assert manifest.dataset_version == "document-rag-backfill-v1"
    assert manifest.split_counts == {"dev": 1, "heldout": 1}
    assert manifest.category_counts == {
        "direct_fact": 1,
        "semantic_paraphrase": 1,
    }
    assert manifest.corpus_manifest_sha256 == "corpus-sha"
    assert len(manifest.dataset_sha256) == 64
