from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.document_corpus_acquire import (
    CorpusSourceSpec,
    build_corpus,
    canonical_units_for_source,
    load_source_specs,
)
from scripts.document_corpus_contract import load_canonical_units, load_corpus_manifest
from scripts.eval_contract import EvaluationContractError


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "document_key": "doc-a",
        "workspace_key": "workspace-a",
        "filename": "doc-a.md",
        "title": "Document A",
        "topic": "test",
        "local_path": "fixtures/doc-a.md",
        "source_mode": "native_text",
        "origin": "real",
    }
    row.update(overrides)
    return row


def test_source_spec_requires_exactly_one_locator() -> None:
    with pytest.raises(EvaluationContractError, match="exactly one"):
        CorpusSourceSpec.from_dict(_row(url="https://example.com/a.pdf"))


def test_remote_url_requires_https() -> None:
    row = _row()
    row.pop("local_path")
    row["url"] = "http://example.com/a.pdf"
    with pytest.raises(EvaluationContractError, match="https"):
        CorpusSourceSpec.from_dict(row)


def test_load_source_specs_rejects_duplicate_filename(tmp_path: Path) -> None:
    path = tmp_path / "sources.jsonl"
    rows = [
        _row(document_key="a", filename="same.md"),
        _row(document_key="b", filename="same.md", local_path="fixtures/b.md"),
    ]
    path.write_text("\n".join(json.dumps(item) for item in rows) + "\n")
    with pytest.raises(EvaluationContractError, match="duplicate filename"):
        load_source_specs(path)


def test_markdown_canonicalization_preserves_sections(tmp_path: Path) -> None:
    source = tmp_path / "doc.md"
    source.write_text("intro\n\n# Alpha\nA body\n## Beta\nB body\n", encoding="utf-8")
    spec = CorpusSourceSpec.from_dict(
        _row(filename="doc.md", local_path="doc.md")
    )
    units = canonical_units_for_source(spec=spec, source_path=source)
    assert [unit.section for unit in units] == ["__preamble__", "Alpha", "Beta"]
    assert units[1].text == "A body"


def test_build_corpus_from_local_sources(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "doc-a.md").write_text("# Alpha\nCanonical fact A.\n", encoding="utf-8")
    (fixtures / "doc-b.txt").write_text("Canonical fact B.\n", encoding="utf-8")

    sources = tmp_path / "sources.jsonl"
    rows = [
        _row(),
        _row(
            document_key="doc-b",
            filename="doc-b.txt",
            title="Document B",
            local_path="fixtures/doc-b.txt",
        ),
    ]
    sources.write_text("\n".join(json.dumps(item) for item in rows) + "\n", encoding="utf-8")

    output = tmp_path / "corpus"
    summary = build_corpus(
        source_spec_path=sources,
        output_root=output,
        corpus_id="test-corpus",
        corpus_version="v1",
    )

    assert summary["document_count"] == 2
    manifest = load_corpus_manifest(output / "corpus_manifest.json")
    assert len(manifest.documents) == 2
    units = load_canonical_units(output / "canonical_units.jsonl")
    assert {unit.document_key for unit in units} == {"doc-a", "doc-b"}
    resolved = [json.loads(line) for line in (output / "resolved_sources.jsonl").read_text(encoding="utf-8").splitlines()]
    assert resolved[0]["source_sha256"]
    assert resolved[0]["canonical_unit_count"] == 1


def test_build_corpus_with_injected_downloader(tmp_path: Path) -> None:
    sources = tmp_path / "sources.jsonl"
    row = _row(
        filename="remote.txt",
        local_path=None,
        url="https://example.com/remote.txt",
    )
    sources.write_text(json.dumps(row) + "\n", encoding="utf-8")

    summary = build_corpus(
        source_spec_path=sources,
        output_root=tmp_path / "corpus",
        corpus_id="remote-corpus",
        corpus_version="v1",
        downloader=lambda _: b"remote canonical text\n",
    )
    assert summary["document_count"] == 1


def test_direct_script_entrypoint_can_import_scripts_namespace() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "scripts/document_corpus_acquire.py", "--help"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--sources" in result.stdout


def test_reuse_frozen_sources_only_when_identity_matches(tmp_path: Path) -> None:
    from scripts.document_corpus_acquire import CorpusSourceSpec, reuse_frozen_sources

    previous = tmp_path / "previous"
    (previous / "sources").mkdir(parents=True)
    (previous / "sources" / "a.pdf").write_bytes(b"frozen-pdf")
    (previous / "resolved_sources.jsonl").write_text(
        json.dumps(
            {
                "document_key": "a",
                "filename": "a.pdf",
                "url": "https://example.test/a.pdf",
                "local_path": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    spec = CorpusSourceSpec.from_dict(
        {
            "document_key": "a",
            "workspace_key": "w",
            "filename": "a.pdf",
            "title": "A",
            "topic": "t",
            "url": "https://example.test/a.pdf",
        }
    )
    output = tmp_path / "new"
    summary = reuse_frozen_sources(
        previous_corpus_root=previous,
        specs=[spec],
        output_root=output,
    )
    assert summary == {"reused_source_count": 1, "reuse_skipped_count": 0}
    assert (output / "sources" / "a.pdf").read_bytes() == b"frozen-pdf"


def test_download_validation_rejects_html_for_pdf() -> None:
    from scripts.document_corpus_acquire import validate_downloaded_bytes

    spec = CorpusSourceSpec.from_dict(
        {
            "document_key": "remote-pdf",
            "workspace_key": "w",
            "filename": "remote.pdf",
            "title": "Remote",
            "topic": "test",
            "url": "https://example.test/remote.pdf",
        }
    )
    with pytest.raises(EvaluationContractError, match="not a PDF"):
        validate_downloaded_bytes(
            spec=spec,
            payload=b"<!doctype html><html><title>abuse detection</title></html>",
        )


def test_build_corpus_collects_failures_before_fail_closed(tmp_path: Path) -> None:
    from scripts.document_corpus_acquire import CorpusBuildIncompleteError

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "good.txt").write_text("good canonical text\n", encoding="utf-8")
    sources = tmp_path / "sources.jsonl"
    rows = [
        {
            "document_key": "good",
            "workspace_key": "w",
            "filename": "good.txt",
            "title": "Good",
            "topic": "test",
            "local_path": "fixtures/good.txt",
        },
        {
            "document_key": "bad",
            "workspace_key": "w",
            "filename": "bad.pdf",
            "title": "Bad",
            "topic": "test",
            "url": "https://example.test/bad.pdf",
        },
    ]
    sources.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    def downloader(url: str) -> bytes:
        assert url.endswith("bad.pdf")
        return b"<!doctype html><html>blocked</html>"

    output = tmp_path / "corpus"
    with pytest.raises(CorpusBuildIncompleteError) as raised:
        build_corpus(
            source_spec_path=sources,
            output_root=output,
            corpus_id="partial",
            corpus_version="v1",
            downloader=downloader,
        )

    summary = raised.value.summary
    assert summary["requested_document_count"] == 2
    assert summary["document_count"] == 1
    assert summary["failed_source_count"] == 1
    assert summary["complete"] is False
    failures = [
        json.loads(line)
        for line in (output / "corpus_build_failures.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert failures[0]["document_key"] == "bad"
    assert (output / "corpus_build_summary.json").is_file()


def test_build_corpus_allow_partial_returns_summary(tmp_path: Path) -> None:
    sources = tmp_path / "sources.jsonl"
    row = {
        "document_key": "bad",
        "workspace_key": "w",
        "filename": "bad.pdf",
        "title": "Bad",
        "topic": "test",
        "url": "https://example.test/bad.pdf",
    }
    sources.write_text(json.dumps(row) + "\n", encoding="utf-8")

    summary = build_corpus(
        source_spec_path=sources,
        output_root=tmp_path / "corpus",
        corpus_id="partial",
        corpus_version="v1",
        downloader=lambda _: b"<html>blocked</html>",
        allow_partial=True,
    )
    assert summary["failed_source_count"] == 1
    assert summary["complete"] is False
