from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


# Support both direct script execution and module execution.
# `python scripts/<name>.py` puts scripts/ rather than the repository root
# on sys.path, so add the repository root before importing via `scripts.*`.
if __package__ in (None, ""):
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

from scripts.document_corpus_contract import (
    load_canonical_units,
    load_corpus_manifest,
    validate_corpus_files,
)
from scripts.eval_contract import EvaluationContractError



_WS = re.compile(r"\s+")


def normalize_evidence_text(text: str) -> str:
    """Normalize only whitespace for PDF-grounding comparisons."""
    return _WS.sub(" ", text).strip()


def evidence_quote_matches_source(*, quote: str, source_text: str) -> bool:
    normalized_quote = normalize_evidence_text(quote)
    normalized_source = normalize_evidence_text(source_text)
    return bool(normalized_quote) and normalized_quote in normalized_source


ALLOWED_CATEGORIES = {
    "direct_fact",
    "semantic_paraphrase",
    "keyword_identifier",
    "section_concept",
}


@dataclass(frozen=True, slots=True)
class QueryGenerationRequest:
    request_id: str
    anchor_id: str
    document_key: str
    topic: str
    page: int | None
    section: str | None
    source_unit_sha256: str
    evidence_text: str
    requested_category: str
    variant: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueryGenerationRequest":
        request = cls(
            request_id=str(data.get("request_id", "")).strip(),
            anchor_id=str(data.get("anchor_id", "")).strip(),
            document_key=str(data.get("document_key", "")).strip(),
            topic=str(data.get("topic", "")).strip(),
            page=(int(data["page"]) if data.get("page") is not None else None),
            section=(
                str(data["section"]).strip()
                if data.get("section") is not None
                else None
            ),
            source_unit_sha256=str(data.get("source_unit_sha256", "")).strip(),
            evidence_text=str(data.get("evidence_text", "")).strip(),
            requested_category=str(data.get("requested_category", "")).strip(),
            variant=str(data.get("variant", "primary")).strip(),
        )
        request.validate()
        return request

    def validate(self) -> None:
        if not self.request_id:
            raise EvaluationContractError("request_id must not be empty")
        if not self.anchor_id:
            raise EvaluationContractError(f"anchor_id missing: {self.request_id}")
        if not self.document_key:
            raise EvaluationContractError(f"document_key missing: {self.request_id}")
        if self.requested_category not in ALLOWED_CATEGORIES:
            raise EvaluationContractError(
                f"invalid requested_category for {self.request_id}: "
                f"{self.requested_category!r}"
            )
        if len(self.source_unit_sha256) != 64:
            raise EvaluationContractError(
                f"invalid source_unit_sha256: {self.request_id}"
            )
        if len(self.evidence_text) < 120:
            raise EvaluationContractError(
                f"evidence_text too short: {self.request_id}"
            )
        if not self.variant:
            raise EvaluationContractError(f"variant missing: {self.request_id}")


@dataclass(frozen=True, slots=True)
class QueryCandidate:
    candidate_id: str
    request_id: str
    anchor_id: str
    document_key: str
    topic: str
    page: int | None
    section: str | None
    source_unit_sha256: str
    category: str
    variant: str
    query: str
    answer_text: str
    evidence_quote: str
    generation_mode: str
    generator_model: str
    batch_id: str
    repair_count: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueryCandidate":
        candidate = cls(
            candidate_id=str(data.get("candidate_id", "")).strip(),
            request_id=str(data.get("request_id", "")).strip(),
            anchor_id=str(data.get("anchor_id", "")).strip(),
            document_key=str(data.get("document_key", "")).strip(),
            topic=str(data.get("topic", "")).strip(),
            page=(int(data["page"]) if data.get("page") is not None else None),
            section=(
                str(data["section"]).strip()
                if data.get("section") is not None
                else None
            ),
            source_unit_sha256=str(data.get("source_unit_sha256", "")).strip(),
            category=str(data.get("category", "")).strip(),
            variant=str(data.get("variant", "")).strip(),
            query=str(data.get("query", "")).strip(),
            answer_text=str(data.get("answer_text", "")).strip(),
            evidence_quote=str(data.get("evidence_quote", "")).strip(),
            generation_mode=str(data.get("generation_mode", "")).strip(),
            generator_model=str(data.get("generator_model", "")).strip(),
            batch_id=str(data.get("batch_id", "")).strip(),
            repair_count=int(data.get("repair_count", 0)),
        )
        candidate.validate()
        return candidate

    def validate(self) -> None:
        if not self.candidate_id:
            raise EvaluationContractError("candidate_id must not be empty")
        if not self.request_id:
            raise EvaluationContractError(f"request_id missing: {self.candidate_id}")
        if self.category not in ALLOWED_CATEGORIES:
            raise EvaluationContractError(
                f"invalid category for {self.candidate_id}: {self.category!r}"
            )
        if len(self.query) < 12 or len(self.query) > 260:
            raise EvaluationContractError(
                f"query length invalid for {self.candidate_id}"
            )
        if not self.query.endswith("?"):
            raise EvaluationContractError(
                f"query must end with ?: {self.candidate_id}"
            )
        if not self.answer_text or len(self.answer_text) > 400:
            raise EvaluationContractError(
                f"answer_text invalid for {self.candidate_id}"
            )
        if len(self.evidence_quote) < 20 or len(self.evidence_quote) > 700:
            raise EvaluationContractError(
                f"evidence_quote length invalid for {self.candidate_id}"
            )
        if self.repair_count < 0:
            raise EvaluationContractError(
                f"repair_count must be non-negative: {self.candidate_id}"
            )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = raw.strip()
        if not line:
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise EvaluationContractError(
                f"expected object at {path}:{line_number}"
            )
        rows.append(payload)
    return rows


def load_requests(path: Path) -> list[QueryGenerationRequest]:
    rows = _load_jsonl(path)
    requests = [QueryGenerationRequest.from_dict(row) for row in rows]
    ids = [item.request_id for item in requests]
    if len(set(ids)) != len(ids):
        raise EvaluationContractError("duplicate request_id in query generation plan")
    return requests


def load_candidates(path: Path) -> list[QueryCandidate]:
    rows = _load_jsonl(path)
    candidates = [QueryCandidate.from_dict(row) for row in rows]
    ids = [item.candidate_id for item in candidates]
    if len(set(ids)) != len(ids):
        raise EvaluationContractError("duplicate candidate_id")
    return candidates


def validate_candidates_against_requests(
    *,
    requests: list[QueryGenerationRequest],
    candidates: list[QueryCandidate],
) -> list[str]:
    request_by_id = {item.request_id: item for item in requests}
    errors: list[str] = []
    seen_request_ids: set[str] = set()

    for candidate in candidates:
        request = request_by_id.get(candidate.request_id)
        if request is None:
            errors.append(
                f"{candidate.candidate_id}: unknown request_id {candidate.request_id}"
            )
            continue
        if candidate.request_id in seen_request_ids:
            errors.append(
                f"{candidate.candidate_id}: duplicate output for request "
                f"{candidate.request_id}"
            )
        seen_request_ids.add(candidate.request_id)

        expected_pairs = {
            "anchor_id": (candidate.anchor_id, request.anchor_id),
            "document_key": (candidate.document_key, request.document_key),
            "topic": (candidate.topic, request.topic),
            "page": (candidate.page, request.page),
            "section": (candidate.section, request.section),
            "source_unit_sha256": (
                candidate.source_unit_sha256,
                request.source_unit_sha256,
            ),
            "category": (candidate.category, request.requested_category),
            "variant": (candidate.variant, request.variant),
        }
        for field, (actual, expected) in expected_pairs.items():
            if actual != expected:
                errors.append(
                    f"{candidate.candidate_id}: {field} mismatch "
                    f"expected={expected!r} actual={actual!r}"
                )

        if candidate.evidence_quote not in request.evidence_text:
            errors.append(
                f"{candidate.candidate_id}: evidence_quote is not an exact "
                "substring of anchor evidence"
            )

    return errors


def validate_candidates_against_corpus(
    *,
    corpus_root: Path,
    candidates: list[QueryCandidate],
) -> list[str]:
    manifest_path = corpus_root / "corpus_manifest.json"
    manifest = load_corpus_manifest(manifest_path)
    validate_corpus_files(manifest_path=manifest_path, manifest=manifest)
    units = load_canonical_units(corpus_root / manifest.canonical_units_path)

    units_by_sha: dict[str, list[Any]] = {}
    for unit in units:
        sha = hashlib.sha256(unit.text.encode("utf-8")).hexdigest()
        units_by_sha.setdefault(sha, []).append(unit)

    errors: list[str] = []
    for candidate in candidates:
        sha_matches = units_by_sha.get(candidate.source_unit_sha256, [])
        if not sha_matches:
            errors.append(
                f"{candidate.candidate_id}: source_unit_sha256 not found in frozen corpus"
            )
            continue

        locator_matches = [
            unit
            for unit in sha_matches
            if unit.document_key == candidate.document_key
            and unit.page == candidate.page
            and (unit.section or None) == (candidate.section or None)
        ]
        if not locator_matches:
            observed = [
                {
                    "document_key": unit.document_key,
                    "page": unit.page,
                    "section": unit.section,
                }
                for unit in sha_matches[:5]
            ]
            errors.append(
                f"{candidate.candidate_id}: source SHA exists but locator mismatch; "
                f"candidate={{'document_key': {candidate.document_key!r}, "
                f"'page': {candidate.page!r}, 'section': {candidate.section!r}}} "
                f"observed={observed!r}"
            )
            continue

        if not any(
            evidence_quote_matches_source(
                quote=candidate.evidence_quote,
                source_text=unit.text,
            )
            for unit in locator_matches
        ):
            errors.append(
                f"{candidate.candidate_id}: evidence_quote not found in canonical "
                "source after whitespace normalization"
            )
    return errors


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            json.dump(row, file, ensure_ascii=False)
            file.write("\n")


def write_candidates(path: Path, candidates: list[QueryCandidate]) -> None:
    write_jsonl(path, [asdict(item) for item in candidates])
