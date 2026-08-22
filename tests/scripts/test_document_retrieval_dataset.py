from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.document_retrieval_dataset import (
    DocumentRetrievalCase,
    EvidenceMatchingPolicy,
    build_document_retrieval_manifest,
    load_document_retrieval_cases,
)
from scripts.eval_contract import EvaluationContractError


def base_case(**overrides):
    case = {
        "case_id": "doc-heldout-0001",
        "dataset_version": "document-rag-backfill-v1",
        "split": "heldout",
        "category": "direct_fact",
        "source_origin": "real",
        "review_status": "human_reviewed",
        "notes": "",
        "query": "What is the retention period?",
        "workspace_key": "policy-corpus-v1",
        "answerable": True,
        "source_mode": "native_text",
        "matching_policy": "all",
        "expected_evidence": [
            {
                "document_key": "retention-policy",
                "locator": {"page": 4, "section": "Retention"},
                "contains": "records are retained for seven years",
            }
        ],
    }
    case.update(overrides)
    return case


def test_answerable_case_parses_logical_evidence() -> None:
    case = DocumentRetrievalCase.from_dict(base_case())
    assert case.case_id == "doc-heldout-0001"
    assert case.matching_policy is EvidenceMatchingPolicy.ALL
    assert case.expected_evidence[0].document_key == "retention-policy"
    assert case.expected_evidence[0].locator.page_start == 4


def test_unanswerable_case_requires_no_expected_evidence() -> None:
    case = DocumentRetrievalCase.from_dict(
        base_case(
            case_id="doc-heldout-0002",
            answerable=False,
            matching_policy="none",
            expected_evidence=[],
            category="absent",
        )
    )
    assert case.answerable is False
    assert not case.expected_evidence


def test_unanswerable_case_rejects_evidence() -> None:
    with pytest.raises(EvaluationContractError, match="must not declare expected_evidence"):
        DocumentRetrievalCase.from_dict(
            base_case(answerable=False, matching_policy="none")
        )


def test_required_any_supports_alternative_evidence_per_requirement() -> None:
    case = DocumentRetrievalCase.from_dict(
        base_case(
            case_id="doc-heldout-0003",
            category="multi_chunk",
            matching_policy="required_any",
            expected_evidence=[
                {
                    "document_key": "policy-a",
                    "locator": {"page": 2},
                    "contains": "retention period is seven years",
                    "requirement_key": "retention",
                },
                {
                    "document_key": "policy-a",
                    "locator": {"page": 3},
                    "contains": "seven-year retention requirement",
                    "requirement_key": "retention",
                },
                {
                    "document_key": "policy-b",
                    "locator": {"section": "Deletion"},
                    "contains": "delete records after the retention period",
                    "requirement_key": "deletion",
                },
            ],
        )
    )
    assert case.matching_policy is EvidenceMatchingPolicy.REQUIRED_ANY


def test_required_any_rejects_missing_requirement_key() -> None:
    with pytest.raises(EvaluationContractError, match="requires requirement_key"):
        DocumentRetrievalCase.from_dict(
            base_case(
                matching_policy="required_any",
                expected_evidence=[
                    {
                        "document_key": "policy-a",
                        "locator": {"page": 2},
                        "contains": "seven years",
                        "requirement_key": "retention",
                    },
                    {
                        "document_key": "policy-b",
                        "locator": {"page": 3},
                        "contains": "delete afterwards",
                    },
                ],
            )
        )


def test_locator_rejects_invalid_page_range() -> None:
    with pytest.raises(EvaluationContractError, match="page_end must be >= page_start"):
        DocumentRetrievalCase.from_dict(
            base_case(
                expected_evidence=[
                    {
                        "document_key": "policy-a",
                        "locator": {"page_start": 5, "page_end": 3},
                        "contains": "retention",
                    }
                ]
            )
        )


def test_dataset_manifest_reports_backfill_dimensions(tmp_path: Path) -> None:
    dataset = tmp_path / "document_retrieval_v2.jsonl"
    rows = [
        base_case(),
        base_case(
            case_id="doc-heldout-0002",
            category="ocr_direct_fact",
            source_mode="ocr",
            query="What address is shown?",
            expected_evidence=[
                {
                    "document_key": "scan-001",
                    "locator": {"page": 1},
                    "contains": "1 Example Street",
                }
            ],
        ),
        base_case(
            case_id="doc-heldout-0003",
            category="absent",
            answerable=False,
            matching_policy="none",
            expected_evidence=[],
            query="What is the lunar retention policy?",
        ),
    ]
    dataset.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    manifest = build_document_retrieval_manifest(
        dataset_id="document-rag-backfill",
        dataset_path=dataset,
        expected_dataset_version="document-rag-backfill-v1",
    )

    assert manifest.common.case_count == 3
    assert manifest.answerable_counts == {"answerable": 2, "unanswerable": 1}
    assert manifest.source_mode_counts == {"native_text": 2, "ocr": 1}
    assert manifest.unique_document_key_count == 2
    assert manifest.expected_evidence_unit_count == 2


def test_machine_validated_heldout_is_rejected() -> None:
    with pytest.raises(EvaluationContractError, match="heldout case"):
        DocumentRetrievalCase.from_dict(
            base_case(review_status="machine_validated")
        )


def test_dataset_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    dataset = tmp_path / "dup.jsonl"
    row = base_case()
    dataset.write_text(
        json.dumps(row) + "\n" + json.dumps(row) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(EvaluationContractError, match="duplicate case_id"):
        load_document_retrieval_cases(dataset)
