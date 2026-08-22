import json
from dataclasses import asdict
from pathlib import Path

from scripts.document_corpus_contract import (
    CanonicalDocumentUnit,
    CorpusDocument,
    CorpusDocumentOrigin,
    CorpusManifest,
    sha256_file,
)
from scripts.document_corpus_profile import build_profile
from scripts.document_retrieval_dataset import DocumentSourceMode


def _corpus(tmp_path: Path) -> Path:
    source = tmp_path / "sources" / "a.txt"
    source.parent.mkdir(parents=True)
    source.write_text("hello world", encoding="utf-8")
    units = tmp_path / "canonical_units.jsonl"
    unit = CanonicalDocumentUnit(document_key="a", text="A" * 500, section="document")
    units.write_text(json.dumps(asdict(unit)) + "\n", encoding="utf-8")
    manifest = CorpusManifest(
        corpus_id="c",
        corpus_version="v1",
        documents=(
            CorpusDocument(
                document_key="a",
                workspace_key="w",
                source_path="sources/a.txt",
                source_sha256=sha256_file(source),
                source_mode=DocumentSourceMode.NATIVE_TEXT,
                origin=CorpusDocumentOrigin.REAL,
            ),
        ),
        canonical_units_path=units.name,
        canonical_units_sha256=sha256_file(units),
    )
    (tmp_path / "corpus_manifest.json").write_text(
        json.dumps(manifest.to_dict()), encoding="utf-8"
    )
    (tmp_path / "resolved_sources.jsonl").write_text(
        json.dumps({"document_key": "a", "topic": "testing", "title": "A"}) + "\n",
        encoding="utf-8",
    )
    return tmp_path


def test_profile_reports_topics_and_units(tmp_path: Path) -> None:
    profile = build_profile(corpus_root=_corpus(tmp_path))
    assert profile["document_count"] == 1
    assert profile["canonical_unit_count"] == 1
    assert profile["topic_counts"] == {"testing": 1}
    assert profile["documents"][0]["canonical_character_count"] == 500
