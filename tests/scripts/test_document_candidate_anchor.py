import json
from dataclasses import asdict
from pathlib import Path

from scripts.document_candidate_anchor import build_anchor_pool
from scripts.document_corpus_contract import (
    CanonicalDocumentUnit,
    CorpusDocument,
    CorpusDocumentOrigin,
    CorpusManifest,
    sha256_file,
)
from scripts.document_retrieval_dataset import DocumentSourceMode


def _corpus(tmp_path: Path) -> Path:
    docs = []
    units = []
    resolved = []
    for key in ("a", "b"):
        source = tmp_path / "sources" / f"{key}.txt"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"source-{key}", encoding="utf-8")
        docs.append(
            CorpusDocument(
                document_key=key,
                workspace_key="w",
                source_path=f"sources/{key}.txt",
                source_sha256=sha256_file(source),
                source_mode=DocumentSourceMode.NATIVE_TEXT,
                origin=CorpusDocumentOrigin.REAL,
            )
        )
        for index in range(3):
            units.append(
                CanonicalDocumentUnit(
                    document_key=key,
                    section=f"s{index}",
                    text=(f"{key} meaningful sentence about security controls. " * 15),
                )
            )
        resolved.append({"document_key": key, "topic": f"topic-{key}"})

    units_path = tmp_path / "canonical_units.jsonl"
    units_path.write_text(
        "".join(json.dumps(asdict(item)) + "\n" for item in units),
        encoding="utf-8",
    )
    manifest = CorpusManifest(
        corpus_id="c",
        corpus_version="v1",
        documents=tuple(docs),
        canonical_units_path=units_path.name,
        canonical_units_sha256=sha256_file(units_path),
    )
    (tmp_path / "corpus_manifest.json").write_text(
        json.dumps(manifest.to_dict()), encoding="utf-8"
    )
    (tmp_path / "resolved_sources.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in resolved),
        encoding="utf-8",
    )
    return tmp_path


def test_anchor_pool_balances_documents_and_is_reproducible(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    first, summary = build_anchor_pool(corpus_root=root, target_count=4)
    second, _ = build_anchor_pool(corpus_root=root, target_count=4)
    assert [item.anchor_id for item in first] == [item.anchor_id for item in second]
    assert summary["selected_count"] == 4
    assert summary["document_counts"] == {"a": 2, "b": 2}
    assert all(len(item.evidence_text) >= 200 for item in first)
