from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from scripts.eval_contract import (
    DatasetManifest,
    EvaluationCaseMetadata,
    EvaluationContractError,
    build_dataset_manifest,
    load_jsonl_objects,
)


class DocumentSourceMode(StrEnum):
    NATIVE_TEXT = "native_text"
    OCR = "ocr"
    MIXED = "mixed"


class EvidenceMatchingPolicy(StrEnum):
    ALL = "all"
    ANY = "any"
    REQUIRED_ANY = "required_any"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class SourceLocator:
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SourceLocator":
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise EvaluationContractError("evidence locator must be an object")

        page_start = data.get("page_start", data.get("page"))
        page_end = data.get("page_end", page_start)
        section = data.get("section")

        locator = cls(
            page_start=(int(page_start) if page_start is not None else None),
            page_end=(int(page_end) if page_end is not None else None),
            section=(str(section).strip() if section is not None else None),
        )
        locator.validate()
        return locator

    def validate(self) -> None:
        if self.page_start is not None and self.page_start <= 0:
            raise EvaluationContractError("page_start must be positive")
        if self.page_end is not None and self.page_end <= 0:
            raise EvaluationContractError("page_end must be positive")
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_end < self.page_start
        ):
            raise EvaluationContractError("page_end must be >= page_start")
        if self.page_start is None and self.page_end is not None:
            raise EvaluationContractError("page_end requires page_start")
        if self.page_start is None and not self.section:
            raise EvaluationContractError(
                "evidence locator requires page_start/page or section"
            )


@dataclass(frozen=True, slots=True)
class ExpectedDocumentEvidence:
    document_key: str
    locator: SourceLocator
    contains: str
    requirement_key: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExpectedDocumentEvidence":
        if not isinstance(data, dict):
            raise EvaluationContractError("expected_evidence item must be an object")
        item = cls(
            document_key=str(data.get("document_key", "")).strip(),
            locator=SourceLocator.from_dict(data.get("locator")),
            contains=str(data.get("contains", "")).strip(),
            requirement_key=(
                str(data["requirement_key"]).strip()
                if data.get("requirement_key") is not None
                else None
            ),
        )
        item.validate()
        return item

    def validate(self) -> None:
        if not self.document_key:
            raise EvaluationContractError("evidence document_key must not be empty")
        if not self.contains:
            raise EvaluationContractError("evidence contains must not be empty")
        if self.requirement_key is not None and not self.requirement_key:
            raise EvaluationContractError(
                "evidence requirement_key must be non-empty when provided"
            )

    def identity(self) -> tuple[Any, ...]:
        return (
            self.document_key,
            self.locator.page_start,
            self.locator.page_end,
            self.locator.section,
            self.contains,
            self.requirement_key,
        )


@dataclass(frozen=True, slots=True)
class DocumentRetrievalCase:
    metadata: EvaluationCaseMetadata
    query: str
    workspace_key: str
    answerable: bool
    source_mode: DocumentSourceMode
    expected_evidence: tuple[ExpectedDocumentEvidence, ...]
    matching_policy: EvidenceMatchingPolicy

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DocumentRetrievalCase":
        metadata = EvaluationCaseMetadata.from_dict(data)
        evidence_raw = data.get("expected_evidence", [])
        if not isinstance(evidence_raw, list):
            raise EvaluationContractError(
                f"expected_evidence must be an array: {metadata.case_id}"
            )

        try:
            source_mode = DocumentSourceMode(
                str(data.get("source_mode", "native_text")).strip()
            )
        except ValueError as exc:
            raise EvaluationContractError(
                f"invalid source_mode for {metadata.case_id}: "
                f"{data.get('source_mode')!r}"
            ) from exc

        default_policy = "all" if data.get("answerable") else "none"
        try:
            matching_policy = EvidenceMatchingPolicy(
                str(data.get("matching_policy", default_policy)).strip()
            )
        except ValueError as exc:
            raise EvaluationContractError(
                f"invalid matching_policy for {metadata.case_id}: "
                f"{data.get('matching_policy')!r}"
            ) from exc

        case = cls(
            metadata=metadata,
            query=str(data.get("query", "")).strip(),
            workspace_key=str(data.get("workspace_key", "")).strip(),
            answerable=data.get("answerable") is True,
            source_mode=source_mode,
            expected_evidence=tuple(
                ExpectedDocumentEvidence.from_dict(item)
                for item in evidence_raw
            ),
            matching_policy=matching_policy,
        )
        if not isinstance(data.get("answerable"), bool):
            raise EvaluationContractError(
                f"answerable must be boolean: {metadata.case_id}"
            )
        case.validate()
        return case

    @property
    def case_id(self) -> str:
        return self.metadata.case_id

    def validate(self) -> None:
        if not self.query:
            raise EvaluationContractError(
                f"query must not be empty: {self.case_id}"
            )
        if not self.workspace_key:
            raise EvaluationContractError(
                f"workspace_key must not be empty: {self.case_id}"
            )

        identities = [item.identity() for item in self.expected_evidence]
        if len(set(identities)) != len(identities):
            raise EvaluationContractError(
                f"duplicate expected_evidence item: {self.case_id}"
            )

        if self.answerable:
            if not self.expected_evidence:
                raise EvaluationContractError(
                    f"answerable case requires expected_evidence: {self.case_id}"
                )
            if self.matching_policy is EvidenceMatchingPolicy.NONE:
                raise EvaluationContractError(
                    f"answerable case cannot use matching_policy=none: {self.case_id}"
                )
        else:
            if self.expected_evidence:
                raise EvaluationContractError(
                    f"unanswerable case must not declare expected_evidence: {self.case_id}"
                )
            if self.matching_policy is not EvidenceMatchingPolicy.NONE:
                raise EvaluationContractError(
                    f"unanswerable case must use matching_policy=none: {self.case_id}"
                )

        if self.matching_policy is EvidenceMatchingPolicy.REQUIRED_ANY:
            requirement_keys = [
                item.requirement_key for item in self.expected_evidence
            ]
            if any(not key for key in requirement_keys):
                raise EvaluationContractError(
                    "required_any requires requirement_key on every evidence item: "
                    f"{self.case_id}"
                )
            if len(set(requirement_keys)) < 2:
                raise EvaluationContractError(
                    "required_any requires at least two requirement groups: "
                    f"{self.case_id}"
                )


@dataclass(frozen=True, slots=True)
class DocumentRetrievalDatasetManifest:
    common: DatasetManifest
    answerable_counts: dict[str, int]
    source_mode_counts: dict[str, int]
    matching_policy_counts: dict[str, int]
    unique_workspace_count: int
    unique_document_key_count: int
    expected_evidence_unit_count: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["common"] = self.common.to_dict()
        return payload


def load_document_retrieval_cases(path: Path) -> list[DocumentRetrievalCase]:
    rows = load_jsonl_objects(path)
    cases: list[DocumentRetrievalCase] = []
    seen_case_ids: set[str] = set()
    for row in rows:
        case = DocumentRetrievalCase.from_dict(row)
        if case.case_id in seen_case_ids:
            raise EvaluationContractError(f"duplicate case_id: {case.case_id}")
        seen_case_ids.add(case.case_id)
        cases.append(case)

    versions = {case.metadata.dataset_version for case in cases}
    if len(versions) != 1:
        raise EvaluationContractError(
            "one dataset file must contain exactly one dataset_version: "
            + ", ".join(sorted(versions))
        )
    return cases


def _counter(values: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def build_document_retrieval_manifest(
    *,
    dataset_id: str,
    dataset_path: Path,
    corpus_manifest_sha256: str | None = None,
    expected_dataset_version: str | None = None,
) -> DocumentRetrievalDatasetManifest:
    cases = load_document_retrieval_cases(dataset_path)
    common = build_dataset_manifest(
        dataset_id=dataset_id,
        task="document_retrieval_v2",
        dataset_path=dataset_path,
        corpus_manifest_sha256=corpus_manifest_sha256,
        expected_dataset_version=expected_dataset_version,
    )

    document_keys = {
        evidence.document_key
        for case in cases
        for evidence in case.expected_evidence
    }
    return DocumentRetrievalDatasetManifest(
        common=common,
        answerable_counts=_counter(
            ["answerable" if case.answerable else "unanswerable" for case in cases]
        ),
        source_mode_counts=_counter([case.source_mode.value for case in cases]),
        matching_policy_counts=_counter(
            [case.matching_policy.value for case in cases]
        ),
        unique_workspace_count=len({case.workspace_key for case in cases}),
        unique_document_key_count=len(document_keys),
        expected_evidence_unit_count=sum(
            len(case.expected_evidence) for case in cases
        ),
    )


def write_document_retrieval_manifest(
    *,
    manifest: DocumentRetrievalDatasetManifest,
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a TechPilot Document Retrieval v2 dataset."
    )
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--dataset-version")
    parser.add_argument("--corpus-manifest-sha256")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_document_retrieval_manifest(
        dataset_id=args.dataset_id,
        dataset_path=args.dataset,
        corpus_manifest_sha256=args.corpus_manifest_sha256,
        expected_dataset_version=args.dataset_version,
    )
    if args.output is not None:
        write_document_retrieval_manifest(manifest=manifest, path=args.output)
    print(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
