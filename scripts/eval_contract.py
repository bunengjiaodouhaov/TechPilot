from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable


class EvaluationContractError(ValueError):
    """Raised when an evaluation dataset violates the frozen data contract."""


class EvaluationSplit(StrEnum):
    DEV = "dev"
    HELDOUT = "heldout"
    EXTERNAL = "external"
    ADVERSARIAL = "adversarial"
    STRESS = "stress"


class SourceOrigin(StrEnum):
    REAL = "real"
    DERIVED = "derived"
    SYNTHETIC_FAILURE = "synthetic_failure"


class ReviewStatus(StrEnum):
    MACHINE_VALIDATED = "machine_validated"
    DETERMINISTIC_VALIDATED = "deterministic_validated"
    HUMAN_REVIEWED = "human_reviewed"


@dataclass(frozen=True, slots=True)
class EvaluationCaseMetadata:
    case_id: str
    dataset_version: str
    split: EvaluationSplit
    category: str
    source_origin: SourceOrigin
    review_status: ReviewStatus
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvaluationCaseMetadata":
        raw_case_id = data.get("case_id", data.get("id"))
        if raw_case_id is None:
            raise EvaluationContractError("case must provide case_id (or legacy id)")

        try:
            split = EvaluationSplit(str(data["split"]).strip())
        except KeyError as exc:
            raise EvaluationContractError(f"missing split: {raw_case_id}") from exc
        except ValueError as exc:
            raise EvaluationContractError(
                f"invalid split for {raw_case_id}: {data.get('split')!r}"
            ) from exc

        try:
            source_origin = SourceOrigin(str(data["source_origin"]).strip())
        except KeyError as exc:
            raise EvaluationContractError(
                f"missing source_origin: {raw_case_id}"
            ) from exc
        except ValueError as exc:
            raise EvaluationContractError(
                f"invalid source_origin for {raw_case_id}: "
                f"{data.get('source_origin')!r}"
            ) from exc

        try:
            review_status = ReviewStatus(str(data["review_status"]).strip())
        except KeyError as exc:
            raise EvaluationContractError(
                f"missing review_status: {raw_case_id}"
            ) from exc
        except ValueError as exc:
            raise EvaluationContractError(
                f"invalid review_status for {raw_case_id}: "
                f"{data.get('review_status')!r}"
            ) from exc

        metadata = cls(
            case_id=str(raw_case_id).strip(),
            dataset_version=str(data.get("dataset_version", "")).strip(),
            split=split,
            category=str(data.get("category", "")).strip(),
            source_origin=source_origin,
            review_status=review_status,
            notes=str(data.get("notes", "")).strip(),
        )
        metadata.validate()
        return metadata

    def validate(self) -> None:
        if not self.case_id:
            raise EvaluationContractError("case_id must not be empty")
        if not self.dataset_version:
            raise EvaluationContractError(
                f"dataset_version must not be empty: {self.case_id}"
            )
        if not self.category:
            raise EvaluationContractError(
                f"category must not be empty: {self.case_id}"
            )
        if (
            self.split is EvaluationSplit.HELDOUT
            and self.review_status is ReviewStatus.MACHINE_VALIDATED
        ):
            raise EvaluationContractError(
                "heldout case must be human_reviewed or deterministic_validated: "
                f"{self.case_id}"
            )
        if (
            self.source_origin is SourceOrigin.SYNTHETIC_FAILURE
            and self.split not in {EvaluationSplit.ADVERSARIAL, EvaluationSplit.STRESS}
        ):
            raise EvaluationContractError(
                "synthetic_failure cases belong in adversarial/stress splits: "
                f"{self.case_id}"
            )


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    dataset_id: str
    dataset_version: str
    task: str
    dataset_path: str
    dataset_sha256: str
    case_count: int
    split_counts: dict[str, int]
    category_counts: dict[str, int]
    source_origin_counts: dict[str, int]
    review_status_counts: dict[str, int]
    corpus_manifest_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"evaluation dataset not found: {path}")

    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationContractError(
                f"invalid JSON at {path}:{line_number}: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise EvaluationContractError(
                f"expected JSON object at {path}:{line_number}"
            )
        rows.append(row)

    if not rows:
        raise EvaluationContractError(f"evaluation dataset contains no cases: {path}")
    return rows


def validate_case_metadata(
    rows: Iterable[dict[str, Any]],
    *,
    expected_dataset_version: str | None = None,
) -> list[EvaluationCaseMetadata]:
    metadata: list[EvaluationCaseMetadata] = []
    seen_case_ids: set[str] = set()

    for row in rows:
        item = EvaluationCaseMetadata.from_dict(row)
        if item.case_id in seen_case_ids:
            raise EvaluationContractError(f"duplicate case_id: {item.case_id}")
        seen_case_ids.add(item.case_id)
        if (
            expected_dataset_version is not None
            and item.dataset_version != expected_dataset_version
        ):
            raise EvaluationContractError(
                f"dataset_version mismatch for {item.case_id}: "
                f"expected {expected_dataset_version!r}, "
                f"got {item.dataset_version!r}"
            )
        metadata.append(item)

    versions = {item.dataset_version for item in metadata}
    if len(versions) != 1:
        raise EvaluationContractError(
            "one dataset file must contain exactly one dataset_version: "
            + ", ".join(sorted(versions))
        )
    return metadata


def _counter(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def build_dataset_manifest(
    *,
    dataset_id: str,
    task: str,
    dataset_path: Path,
    corpus_manifest_sha256: str | None = None,
    expected_dataset_version: str | None = None,
) -> DatasetManifest:
    dataset_id = dataset_id.strip()
    task = task.strip()
    if not dataset_id:
        raise EvaluationContractError("dataset_id must not be empty")
    if not task:
        raise EvaluationContractError("task must not be empty")

    rows = load_jsonl_objects(dataset_path)
    metadata = validate_case_metadata(
        rows,
        expected_dataset_version=expected_dataset_version,
    )
    dataset_version = metadata[0].dataset_version

    return DatasetManifest(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        task=task,
        dataset_path=str(dataset_path),
        dataset_sha256=sha256_file(dataset_path),
        case_count=len(metadata),
        split_counts=_counter(item.split.value for item in metadata),
        category_counts=_counter(item.category for item in metadata),
        source_origin_counts=_counter(item.source_origin.value for item in metadata),
        review_status_counts=_counter(item.review_status.value for item in metadata),
        corpus_manifest_sha256=corpus_manifest_sha256,
    )


def write_manifest(*, manifest: DatasetManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
