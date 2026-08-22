from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import document_corpus_preflight as mod
from scripts.eval_contract import EvaluationContractError


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _row(key: str, url: str) -> dict:
    return {
        "document_key": key,
        "workspace_key": "w",
        "filename": f"{key}.pdf",
        "title": key,
        "topic": "t",
        "url": url,
    }


def test_fallback_fills_rejected_primary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    primary = tmp_path / "primary.jsonl"
    fallback = tmp_path / "fallback.jsonl"
    _write(primary, [_row("a", "https://x/a.pdf"), _row("b", "https://x/b.pdf")])
    _write(fallback, [_row("c", "https://x/c.pdf")])

    def fake_probe(spec, *, timeout_s):
        if spec.document_key == "b":
            raise EvaluationContractError("404")
        return {"status": "remote-ok", "final_url": spec.url, "content_type": "application/pdf"}

    monkeypatch.setattr(mod, "_probe_remote", fake_probe)
    summary = mod.run_preflight(
        primary=primary,
        fallbacks=[fallback],
        output_dir=tmp_path / "out",
        target_count=2,
        reuse_corpus_root=None,
        timeout_s=1,
    )
    assert summary["accepted_count"] == 2
    assert summary["rejected_count"] == 1
    rows = [json.loads(line) for line in (tmp_path / "out/resolved_source_seed.jsonl").read_text().splitlines()]
    assert [row["document_key"] for row in rows] == ["a", "c"]


def test_target_not_met_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    primary = tmp_path / "primary.jsonl"
    _write(primary, [_row("a", "https://x/a.pdf")])
    monkeypatch.setattr(mod, "_probe_remote", lambda *args, **kwargs: (_ for _ in ()).throw(EvaluationContractError("bad")))
    with pytest.raises(EvaluationContractError, match="target not met"):
        mod.run_preflight(
            primary=primary,
            fallbacks=[],
            output_dir=tmp_path / "out",
            target_count=1,
            reuse_corpus_root=None,
            timeout_s=1,
        )
