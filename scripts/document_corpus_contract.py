from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from scripts.document_retrieval_dataset import (
    DocumentRetrievalCase,
    DocumentSourceMode,
    ExpectedDocumentEvidence,
    load_document_retrieval_cases,
)
from scripts.eval_contract import EvaluationContractError


class CorpusDocumentOrigin(StrEnum):
    REAL = "real"
    DERIVED = "derived"


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    document_key: str
    workspace_key: str
    source_path: str
    source_sha256: str
    source_mode: DocumentSourceMode
    origin: CorpusDocumentOrigin
    variant_of: str | None = None
    source_url: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CorpusDocument":
        try:
            source_mode = DocumentSourceMode(str(data["source_mode"]).strip())
        except KeyError as exc:
            raise EvaluationContractError("corpus document missing source_mode") from exc
        except ValueError as exc:
            raise EvaluationContractError(
                f"invalid corpus source_mode: {data.get('source_mode')!r}"
            ) from exc

        try:
            origin = CorpusDocumentOrigin(str(data["origin"]).strip())
        except KeyError as exc:
            raise EvaluationContractError("corpus document missing origin") from exc
        except ValueError as exc:
            raise EvaluationContractError(
                f"invalid corpus origin: {data.get('origin')!r}"
            ) from exc

        item = cls(
            document_key=str(data.get("document_key", "")).strip(),
            workspace_key=str(data.get("workspace_key", "")).strip(),
            source_path=str(data.get("source_path", "")).strip(),
            source_sha256=str(data.get("source_sha256", "")).strip().lower(),
            source_mode=source_mode,
            origin=origin,
            variant_of=(
                str(data["variant_of"]).strip()
                if data.get("variant_of") is not None
                else None
            ),
            source_url=(
                str(data["source_url"]).strip()
                if data.get("source_url") is not None
                else None
            ),
        )
        item.validate()
        return item

    def validate(self) -> None:
        if not self.document_key:
            raise EvaluationContractError("corpus document_key must not be empty")
        if not self.workspace_key:
            raise EvaluationContractError(
                f"corpus workspace_key must not be empty: {self.document_key}"
            )
        if not self.source_path:
            raise EvaluationContractError(
                f"corpus source_path must not be empty: {self.document_key}"
            )
        if len(self.source_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.source_sha256
        ):
            raise EvaluationContractError(
                f"invalid source_sha256: {self.document_key}"
            )
        if self.origin is CorpusDocumentOrigin.DERIVED and not self.variant_of:
            raise EvaluationContractError(
                f"derived document requires variant_of: {self.document_key}"
            )
        if self.origin is CorpusDocumentOrigin.REAL and self.variant_of is not None:
            raise EvaluationContractError(
                f"real document cannot declare variant_of: {self.document_key}"
            )


@dataclass(frozen=True, slots=True)
class CanonicalDocumentUnit:
    document_key: str
    text: str
    page: int | None = None
    section: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CanonicalDocumentUnit":
        page = data.get("page")
        item = cls(
            document_key=str(data.get("document_key", "")).strip(),
            text=str(data.get("text", "")),
            page=(int(page) if page is not None else None),
            section=(
                str(data["section"]).strip()
                if data.get("section") is not None
                else None
            ),
        )
        item.validate()
        return item

    def validate(self) -> None:
        if not self.document_key:
            raise EvaluationContractError("canonical unit document_key must not be empty")
        if not self.text.strip():
            raise EvaluationContractError(
                f"canonical unit text must not be empty: {self.document_key}"
            )
        if self.page is not None and self.page <= 0:
            raise EvaluationContractError(
                f"canonical unit page must be positive: {self.document_key}"
            )
        if self.page is None and not self.section:
            raise EvaluationContractError(
                "canonical unit requires page or section: "
                f"{self.document_key}"
            )


@dataclass(frozen=True, slots=True)
class CorpusManifest:
    corpus_id: str
    corpus_version: str
    documents: tuple[CorpusDocument, ...]
    canonical_units_path: str
    canonical_units_sha256: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CorpusManifest":
        documents_raw = data.get("documents")
        if not isinstance(documents_raw, list) or not documents_raw:
            raise EvaluationContractError("corpus manifest documents must be non-empty")
        manifest = cls(
            corpus_id=str(data.get("corpus_id", "")).strip(),
            corpus_version=str(data.get("corpus_version", "")).strip(),
            documents=tuple(CorpusDocument.from_dict(item) for item in documents_raw),
            canonical_units_path=str(data.get("canonical_units_path", "")).strip(),
            canonical_units_sha256=str(data.get("canonical_units_sha256", "")).strip().lower(),
        )
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if not self.corpus_id:
            raise EvaluationContractError("corpus_id must not be empty")
        if not self.corpus_version:
            raise EvaluationContractError("corpus_version must not be empty")
        keys = [item.document_key for item in self.documents]
        if len(set(keys)) != len(keys):
            raise EvaluationContractError("duplicate corpus document_key")
        known = set(keys)
        for item in self.documents:
            if item.variant_of is not None and item.variant_of not in known:
                raise EvaluationContractError(
                    f"variant_of does not exist for {item.document_key}: {item.variant_of}"
                )
        if not self.canonical_units_path:
            raise EvaluationContractError("canonical_units_path must not be empty")
        if len(self.canonical_units_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.canonical_units_sha256
        ):
            raise EvaluationContractError("invalid canonical_units_sha256")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_corpus_manifest(path: Path) -> CorpusManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise EvaluationContractError("corpus manifest must be a JSON object")
    return CorpusManifest.from_dict(payload)


def load_canonical_units(path: Path) -> list[CanonicalDocumentUnit]:
    if not path.is_file():
        raise FileNotFoundError(f"canonical units not found: {path}")
    units: list[CanonicalDocumentUnit] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise EvaluationContractError(
                f"canonical unit must be object at {path}:{line_number}"
            )
        units.append(CanonicalDocumentUnit.from_dict(payload))
    if not units:
        raise EvaluationContractError("canonical units file contains no units")
    return units


def validate_corpus_files(*, manifest_path: Path, manifest: CorpusManifest) -> None:
    root = manifest_path.parent
    units_path = (root / manifest.canonical_units_path).resolve()
    if not units_path.is_file():
        raise EvaluationContractError(f"canonical_units_path does not exist: {units_path}")
    if sha256_file(units_path) != manifest.canonical_units_sha256:
        raise EvaluationContractError("canonical units SHA256 mismatch")

    for document in manifest.documents:
        source_path = (root / document.source_path).resolve()
        if not source_path.is_file():
            raise EvaluationContractError(
                f"corpus source_path does not exist: {document.document_key}: {source_path}"
            )
        if sha256_file(source_path) != document.source_sha256:
            raise EvaluationContractError(
                f"source SHA256 mismatch: {document.document_key}"
            )


def validate_canonical_units(
    *,
    manifest: CorpusManifest,
    units: list[CanonicalDocumentUnit],
) -> None:
    known = {item.document_key for item in manifest.documents}
    by_document: dict[str, int] = {key: 0 for key in known}
    for unit in units:
        if unit.document_key not in known:
            raise EvaluationContractError(
                f"canonical unit references unknown document: {unit.document_key}"
            )
        by_document[unit.document_key] += 1
    missing = sorted(key for key, count in by_document.items() if count == 0)
    if missing:
        raise EvaluationContractError(
            "corpus documents missing canonical units: " + ", ".join(missing)
        )


def _units_for_evidence(
    *,
    evidence: ExpectedDocumentEvidence,
    units: list[CanonicalDocumentUnit],
) -> list[CanonicalDocumentUnit]:
    candidates = [unit for unit in units if unit.document_key == evidence.document_key]
    locator = evidence.locator
    if locator.page_start is not None:
        candidates = [
            unit
            for unit in candidates
            if unit.page is not None
            and locator.page_start <= unit.page <= (locator.page_end or locator.page_start)
        ]
    if locator.section:
        section = locator.section.casefold()
        candidates = [
            unit
            for unit in candidates
            if unit.section is not None and section in unit.section.casefold()
        ]
    return candidates


def validate_expected_evidence_against_corpus(
    *,
    cases: list[DocumentRetrievalCase],
    manifest: CorpusManifest,
    units: list[CanonicalDocumentUnit],
) -> None:
    documents = {item.document_key: item for item in manifest.documents}
    for case in cases:
        for evidence in case.expected_evidence:
            document = documents.get(evidence.document_key)
            if document is None:
                raise EvaluationContractError(
                    f"case {case.case_id} references unknown document_key: "
                    f"{evidence.document_key}"
                )
            if case.workspace_key != document.workspace_key:
                raise EvaluationContractError(
                    f"workspace mismatch for {case.case_id}/{evidence.document_key}: "
                    f"case={case.workspace_key}, corpus={document.workspace_key}"
                )
            if case.source_mode is not DocumentSourceMode.MIXED:
                if document.source_mode is not case.source_mode:
                    raise EvaluationContractError(
                        f"source_mode mismatch for {case.case_id}/{evidence.document_key}: "
                        f"case={case.source_mode.value}, corpus={document.source_mode.value}"
                    )

            relevant_units = _units_for_evidence(evidence=evidence, units=units)
            if not relevant_units:
                raise EvaluationContractError(
                    f"locator matches no canonical units: {case.case_id}/{evidence.document_key}"
                )
            haystack = "\n".join(unit.text for unit in relevant_units)
            if evidence.contains not in haystack:
                raise EvaluationContractError(
                    f"authoritative substring not found: {case.case_id}/{evidence.document_key}"
                )


def validate_document_dataset_against_corpus(
    *,
    dataset_path: Path,
    corpus_manifest_path: Path,
) -> dict[str, Any]:
    cases = load_document_retrieval_cases(dataset_path)
    manifest = load_corpus_manifest(corpus_manifest_path)
    validate_corpus_files(manifest_path=corpus_manifest_path, manifest=manifest)
    units_path = (corpus_manifest_path.parent / manifest.canonical_units_path).resolve()
    units = load_canonical_units(units_path)
    validate_canonical_units(manifest=manifest, units=units)
    validate_expected_evidence_against_corpus(
        cases=cases,
        manifest=manifest,
        units=units,
    )
    return {
        "dataset_cases": len(cases),
        "corpus_id": manifest.corpus_id,
        "corpus_version": manifest.corpus_version,
        "corpus_documents": len(manifest.documents),
        "canonical_units": len(units),
        "validated_expected_evidence_units": sum(
            len(case.expected_evidence) for case in cases
        ),
        "status": "PASS",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate Document Retrieval v2 Golden against canonical corpus text."
    )
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--corpus-manifest", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = validate_document_dataset_against_corpus(
        dataset_path=args.dataset,
        corpus_manifest_path=args.corpus_manifest,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
