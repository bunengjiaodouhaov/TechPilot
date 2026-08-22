from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.document_corpus_contract import validate_document_dataset_against_corpus
from scripts.eval_contract import EvaluationContractError


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_fixture(tmp_path: Path, *, contains: str = "seven years", mode: str = "native_text"):
    source = tmp_path / "policy.txt"
    source.write_text("source bytes", encoding="utf-8")
    units = tmp_path / "canonical_units.jsonl"
    units.write_text(
        json.dumps(
            {
                "document_key": "retention-policy",
                "page": 4,
                "section": "Retention",
                "text": "The records retention period is seven years.",
            }
        ) + "\n",
        encoding="utf-8",
    )
    corpus = tmp_path / "corpus_manifest.json"
    corpus.write_text(
        json.dumps(
            {
                "corpus_id": "doc-backfill",
                "corpus_version": "v1",
                "canonical_units_path": "canonical_units.jsonl",
                "canonical_units_sha256": sha(units),
                "documents": [
                    {
                        "document_key": "retention-policy",
                        "workspace_key": "policy-corpus-v1",
                        "source_path": "policy.txt",
                        "source_sha256": sha(source),
                        "source_mode": mode,
                        "origin": "real",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "case_id": "doc-heldout-0001",
                "dataset_version": "document-rag-backfill-v1",
                "split": "heldout",
                "category": "direct_fact",
                "source_origin": "real",
                "review_status": "human_reviewed",
                "query": "What is the retention period?",
                "workspace_key": "policy-corpus-v1",
                "answerable": True,
                "source_mode": mode,
                "matching_policy": "all",
                "expected_evidence": [
                    {
                        "document_key": "retention-policy",
                        "locator": {"page": 4, "section": "Retention"},
                        "contains": contains,
                    }
                ],
            }
        ) + "\n",
        encoding="utf-8",
    )
    return dataset, corpus


def test_validates_authoritative_substring_and_locator(tmp_path: Path) -> None:
    dataset, corpus = write_fixture(tmp_path)
    report = validate_document_dataset_against_corpus(
        dataset_path=dataset,
        corpus_manifest_path=corpus,
    )
    assert report["status"] == "PASS"
    assert report["validated_expected_evidence_units"] == 1


def test_rejects_substring_not_in_canonical_source(tmp_path: Path) -> None:
    dataset, corpus = write_fixture(tmp_path, contains="nine years")
    with pytest.raises(EvaluationContractError, match="substring not found"):
        validate_document_dataset_against_corpus(
            dataset_path=dataset,
            corpus_manifest_path=corpus,
        )


def test_rejects_locator_without_matching_unit(tmp_path: Path) -> None:
    dataset, corpus = write_fixture(tmp_path)
    row = json.loads(dataset.read_text())
    row["expected_evidence"][0]["locator"]["page"] = 8
    dataset.write_text(json.dumps(row) + "\n")
    with pytest.raises(EvaluationContractError, match="locator matches no canonical units"):
        validate_document_dataset_against_corpus(
            dataset_path=dataset,
            corpus_manifest_path=corpus,
        )


def test_rejects_workspace_mismatch(tmp_path: Path) -> None:
    dataset, corpus = write_fixture(tmp_path)
    row = json.loads(dataset.read_text())
    row["workspace_key"] = "other-workspace"
    dataset.write_text(json.dumps(row) + "\n")
    with pytest.raises(EvaluationContractError, match="workspace mismatch"):
        validate_document_dataset_against_corpus(
            dataset_path=dataset,
            corpus_manifest_path=corpus,
        )


def test_rejects_source_mode_mismatch(tmp_path: Path) -> None:
    dataset, corpus = write_fixture(tmp_path)
    row = json.loads(dataset.read_text())
    row["source_mode"] = "ocr"
    dataset.write_text(json.dumps(row) + "\n")
    with pytest.raises(EvaluationContractError, match="source_mode mismatch"):
        validate_document_dataset_against_corpus(
            dataset_path=dataset,
            corpus_manifest_path=corpus,
        )


def test_rejects_tampered_source_sha(tmp_path: Path) -> None:
    dataset, corpus = write_fixture(tmp_path)
    (tmp_path / "policy.txt").write_text("tampered", encoding="utf-8")
    with pytest.raises(EvaluationContractError, match="source SHA256 mismatch"):
        validate_document_dataset_against_corpus(
            dataset_path=dataset,
            corpus_manifest_path=corpus,
        )


def test_derived_ocr_document_requires_variant_of(tmp_path: Path) -> None:
    dataset, corpus = write_fixture(tmp_path, mode="ocr")
    payload = json.loads(corpus.read_text())
    payload["documents"][0]["origin"] = "derived"
    corpus.write_text(json.dumps(payload))
    with pytest.raises(EvaluationContractError, match="derived document requires variant_of"):
        validate_document_dataset_against_corpus(
            dataset_path=dataset,
            corpus_manifest_path=corpus,
        )
