from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import sys

if __package__ in (None, ""):
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

from scripts.document_corpus_contract import (
    CanonicalDocumentUnit,
    load_canonical_units,
    load_corpus_manifest,
    validate_corpus_files,
)
from scripts.eval_contract import EvaluationContractError


@dataclass(frozen=True, slots=True)
class CandidateAnchor:
    anchor_id: str
    document_key: str
    topic: str
    page: int | None
    section: str | None
    source_unit_sha256: str
    evidence_text: str
    evidence_character_count: int
    selection_score: str


_WS = re.compile(r"\s+")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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


def _stable_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _clean_excerpt(text: str, *, max_chars: int) -> str:
    normalized = _WS.sub(" ", text).strip()
    if len(normalized) <= max_chars:
        return normalized
    clipped = normalized[:max_chars]
    last_stop = max(clipped.rfind(". "), clipped.rfind("? "), clipped.rfind("! "))
    if last_stop >= max_chars // 2:
        return clipped[: last_stop + 1].strip()
    return clipped.rstrip()


def _looks_low_value(text: str) -> bool:
    normalized = _WS.sub(" ", text).strip()
    if len(normalized) < 160:
        return True
    alpha = sum(char.isalpha() for char in normalized)
    if alpha / max(1, len(normalized)) < 0.45:
        return True
    lowered = normalized.lower()
    toc_markers = ("table of contents", "contents ................................................................")
    if any(marker in lowered for marker in toc_markers):
        return True
    return False


def build_anchor_pool(
    *,
    corpus_root: Path,
    target_count: int,
    min_unit_chars: int = 250,
    max_unit_chars: int = 12000,
    excerpt_chars: int = 1400,
) -> tuple[list[CandidateAnchor], dict[str, Any]]:
    if target_count <= 0:
        raise EvaluationContractError("target_count must be positive")
    if min_unit_chars <= 0 or max_unit_chars < min_unit_chars:
        raise EvaluationContractError("invalid unit character bounds")
    if excerpt_chars < 200:
        raise EvaluationContractError("excerpt_chars must be at least 200")

    manifest_path = corpus_root / "corpus_manifest.json"
    manifest = load_corpus_manifest(manifest_path)
    validate_corpus_files(manifest_path=manifest_path, manifest=manifest)
    units = load_canonical_units(corpus_root / manifest.canonical_units_path)
    resolved = _load_jsonl(corpus_root / "resolved_sources.jsonl")
    topic_by_doc = {
        str(row["document_key"]): str(row.get("topic", "unknown"))
        for row in resolved
    }

    eligible_by_doc: dict[str, list[tuple[str, CanonicalDocumentUnit]]] = defaultdict(list)
    rejected_low_value = 0
    rejected_length = 0
    for unit in units:
        length = len(unit.text)
        if length < min_unit_chars or length > max_unit_chars:
            rejected_length += 1
            continue
        if _looks_low_value(unit.text):
            rejected_low_value += 1
            continue
        unit_identity = _stable_hash(
            manifest.corpus_version,
            unit.document_key,
            str(unit.page),
            str(unit.section),
            unit.text,
        )
        eligible_by_doc[unit.document_key].append((unit_identity, unit))

    for items in eligible_by_doc.values():
        items.sort(key=lambda pair: pair[0])

    document_keys = [
        item.document_key
        for item in manifest.documents
        if eligible_by_doc.get(item.document_key)
    ]
    if not document_keys:
        raise EvaluationContractError("no eligible canonical units for anchor generation")

    # Round-robin across documents first, then stable-hash order within each
    # document. This prevents a few very long PDFs from dominating the pool.
    selected_pairs: list[tuple[str, CanonicalDocumentUnit]] = []
    index = 0
    while len(selected_pairs) < target_count:
        progressed = False
        for document_key in document_keys:
            items = eligible_by_doc[document_key]
            if index < len(items):
                selected_pairs.append(items[index])
                progressed = True
                if len(selected_pairs) >= target_count:
                    break
        if not progressed:
            break
        index += 1

    anchors: list[CandidateAnchor] = []
    topic_counts: Counter[str] = Counter()
    document_counts: Counter[str] = Counter()
    for ordinal, (unit_hash, unit) in enumerate(selected_pairs, 1):
        topic = topic_by_doc.get(unit.document_key, "unknown")
        evidence = _clean_excerpt(unit.text, max_chars=excerpt_chars)
        anchor_id = f"doc-anchor-{ordinal:04d}-{unit_hash[:10]}"
        anchors.append(
            CandidateAnchor(
                anchor_id=anchor_id,
                document_key=unit.document_key,
                topic=topic,
                page=unit.page,
                section=unit.section,
                source_unit_sha256=hashlib.sha256(unit.text.encode("utf-8")).hexdigest(),
                evidence_text=evidence,
                evidence_character_count=len(evidence),
                selection_score=unit_hash,
            )
        )
        topic_counts[topic] += 1
        document_counts[unit.document_key] += 1

    summary = {
        "corpus_id": manifest.corpus_id,
        "corpus_version": manifest.corpus_version,
        "target_count": target_count,
        "selected_count": len(anchors),
        "eligible_document_count": len(document_keys),
        "eligible_unit_count": sum(len(items) for items in eligible_by_doc.values()),
        "rejected_length_count": rejected_length,
        "rejected_low_value_count": rejected_low_value,
        "topic_counts": dict(sorted(topic_counts.items())),
        "document_counts": dict(sorted(document_counts.items())),
        "selection_method": "stable_hash_round_robin_v1",
        "min_unit_chars": min_unit_chars,
        "max_unit_chars": max_unit_chars,
        "excerpt_chars": excerpt_chars,
    }
    return anchors, summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build reproducible source-evidence anchors for document retrieval dataset authoring."
    )
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--target-count", type=int, default=480)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--min-unit-chars", type=int, default=250)
    parser.add_argument("--max-unit-chars", type=int, default=12000)
    parser.add_argument("--excerpt-chars", type=int, default=1400)
    args = parser.parse_args()

    anchors, summary = build_anchor_pool(
        corpus_root=args.corpus_root,
        target_count=args.target_count,
        min_unit_chars=args.min_unit_chars,
        max_unit_chars=args.max_unit_chars,
        excerpt_chars=args.excerpt_chars,
    )
    output_dir = args.output_dir or args.corpus_root
    output_dir.mkdir(parents=True, exist_ok=True)
    anchors_path = output_dir / "candidate_anchors.jsonl"
    with anchors_path.open("w", encoding="utf-8") as file:
        for anchor in anchors:
            json.dump(asdict(anchor), file, ensure_ascii=False)
            file.write("\n")
    summary_path = output_dir / "candidate_anchor_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"anchors: {anchors_path}")
    print(f"summary: {summary_path}")


if __name__ == "__main__":
    main()
