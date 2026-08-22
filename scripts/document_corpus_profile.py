from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
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


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_profile(*, corpus_root: Path) -> dict[str, Any]:
    manifest_path = corpus_root / "corpus_manifest.json"
    manifest = load_corpus_manifest(manifest_path)
    validate_corpus_files(manifest_path=manifest_path, manifest=manifest)
    units = load_canonical_units(corpus_root / manifest.canonical_units_path)

    resolved_path = corpus_root / "resolved_sources.jsonl"
    resolved = _load_jsonl(resolved_path)
    by_key = {str(row["document_key"]): row for row in resolved}

    units_by_doc: dict[str, list[CanonicalDocumentUnit]] = defaultdict(list)
    for unit in units:
        units_by_doc[unit.document_key].append(unit)

    document_rows: list[dict[str, Any]] = []
    topic_counts: Counter[str] = Counter()
    all_lengths: list[int] = []
    for document in manifest.documents:
        source = by_key.get(document.document_key, {})
        document_units = units_by_doc.get(document.document_key, [])
        lengths = [len(item.text) for item in document_units]
        all_lengths.extend(lengths)
        topic = str(source.get("topic", "unknown"))
        topic_counts[topic] += 1
        pages = sorted({item.page for item in document_units if item.page is not None})
        sections = sorted({item.section for item in document_units if item.section})
        document_rows.append(
            {
                "document_key": document.document_key,
                "title": source.get("title"),
                "topic": topic,
                "source_mode": document.source_mode.value,
                "origin": document.origin.value,
                "canonical_unit_count": len(document_units),
                "canonical_character_count": sum(lengths),
                "page_count_with_text": len(pages),
                "section_count": len(sections),
                "unit_char_min": min(lengths) if lengths else 0,
                "unit_char_median": median(lengths) if lengths else 0,
                "unit_char_mean": mean(lengths) if lengths else 0,
                "unit_char_max": max(lengths) if lengths else 0,
                "source_sha256": document.source_sha256,
            }
        )

    profile = {
        "corpus_id": manifest.corpus_id,
        "corpus_version": manifest.corpus_version,
        "document_count": len(manifest.documents),
        "canonical_unit_count": len(units),
        "canonical_character_count": sum(all_lengths),
        "topic_counts": dict(sorted(topic_counts.items())),
        "unit_char_min": min(all_lengths) if all_lengths else 0,
        "unit_char_median": median(all_lengths) if all_lengths else 0,
        "unit_char_mean": mean(all_lengths) if all_lengths else 0,
        "unit_char_max": max(all_lengths) if all_lengths else 0,
        "canonical_units_sha256": manifest.canonical_units_sha256,
        "profile_basis_sha256": _sha256_text(
            manifest.canonical_units_sha256
            + "|"
            + "|".join(sorted(item.source_sha256 for item in manifest.documents))
        ),
        "documents": document_rows,
    }
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile a frozen TechPilot document evaluation corpus."
    )
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    profile = build_profile(corpus_root=args.corpus_root)
    output = args.output or (args.corpus_root / "corpus_profile.json")
    output.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(profile, ensure_ascii=False, indent=2))
    print(f"profile: {output}")


if __name__ == "__main__":
    main()
