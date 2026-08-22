from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

if __package__ in (None, ""):
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

import httpx

from scripts.document_corpus_acquire import (
    ACQUIRE_SCRIPT_VERSION,
    CorpusSourceSpec,
    load_source_specs,
)
from scripts.eval_contract import EvaluationContractError

PREFLIGHT_SCRIPT_VERSION = "phase-b2-preflight-v1"


def _headers(url: str) -> dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"
        ),
        "Accept": "application/pdf,text/plain,text/markdown;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    host = (urlparse(url).hostname or "").lower()
    if host == "fda.gov" or host.endswith(".fda.gov"):
        headers["Referer"] = "https://www.fda.gov/"
    return headers


def _probe_remote(spec: CorpusSourceSpec, *, timeout_s: float = 20.0) -> dict[str, Any]:
    assert spec.url is not None
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout_s),
            follow_redirects=True,
            headers=_headers(spec.url),
        ) as client:
            with client.stream("GET", spec.url) as response:
                response.raise_for_status()
                prefix = b""
                for chunk in response.iter_bytes():
                    prefix += chunk
                    if len(prefix) >= 4096:
                        break
    except Exception as exc:
        raise EvaluationContractError(
            f"preflight failed: {spec.document_key}: {type(exc).__name__}: {exc}"
        ) from exc

    if not prefix:
        raise EvaluationContractError(
            f"preflight empty response: {spec.document_key}: {spec.url}"
        )
    suffix = Path(spec.filename).suffix.lower()
    lowered = prefix.lstrip().lower()
    if suffix == ".pdf" and not prefix.lstrip().startswith(b"%PDF-"):
        kind = "html" if lowered.startswith(b"<!doctype html") or lowered.startswith(b"<html") else "non-pdf"
        raise EvaluationContractError(
            f"preflight payload is {kind}, not PDF: {spec.document_key}: {spec.url}"
        )
    if suffix in {".md", ".txt"} and (
        lowered.startswith(b"<!doctype html") or lowered.startswith(b"<html")
    ):
        raise EvaluationContractError(
            f"preflight payload is HTML, not text source: {spec.document_key}: {spec.url}"
        )
    return {
        "status": "remote-ok",
        "final_url": str(response.url),
        "content_type": response.headers.get("content-type"),
    }


def _load_previous_resolved(previous_corpus_root: Path | None) -> dict[str, dict[str, Any]]:
    if previous_corpus_root is None:
        return {}
    path = previous_corpus_root / "resolved_sources.jsonl"
    if not path.is_file():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            row = json.loads(raw)
            rows[str(row.get("document_key", ""))] = row
    return rows


def _reusable(spec: CorpusSourceSpec, previous_root: Path | None, previous_rows: dict[str, dict[str, Any]]) -> bool:
    if previous_root is None:
        return False
    row = previous_rows.get(spec.document_key)
    if row is None:
        return False
    if row.get("filename") != spec.filename or row.get("url") != spec.url or row.get("local_path") != spec.local_path:
        return False
    source = previous_root / "sources" / spec.filename
    return source.is_file() and source.stat().st_size > 0


def _spec_to_json(spec: CorpusSourceSpec) -> dict[str, Any]:
    row = asdict(spec)
    row["source_mode"] = spec.source_mode.value
    row["origin"] = spec.origin.value
    return {k: v for k, v in row.items() if v is not None}


def _merge_specs(paths: list[Path]) -> list[CorpusSourceSpec]:
    merged: list[CorpusSourceSpec] = []
    seen_keys: set[str] = set()
    seen_urls: set[str] = set()
    for path in paths:
        for spec in load_source_specs(path):
            if spec.document_key in seen_keys:
                continue
            if spec.url is not None and spec.url in seen_urls:
                continue
            seen_keys.add(spec.document_key)
            if spec.url is not None:
                seen_urls.add(spec.url)
            merged.append(spec)
    return merged


def run_preflight(
    *,
    primary: Path,
    fallbacks: list[Path],
    output_dir: Path,
    target_count: int,
    reuse_corpus_root: Path | None,
    timeout_s: float,
) -> dict[str, Any]:
    if target_count <= 0:
        raise EvaluationContractError("target_count must be > 0")
    primary_specs = _merge_specs([primary])
    fallback_specs = _merge_specs(fallbacks)
    primary_keys = {item.document_key for item in primary_specs}
    fallback_specs = [item for item in fallback_specs if item.document_key not in primary_keys]

    previous_rows = _load_previous_resolved(reuse_corpus_root)
    accepted: list[CorpusSourceSpec] = []
    rejected: list[dict[str, Any]] = []
    probe_rows: list[dict[str, Any]] = []

    def process(spec: CorpusSourceSpec, source_group: str) -> bool:
        if _reusable(spec, reuse_corpus_root, previous_rows):
            accepted.append(spec)
            probe_rows.append({
                "document_key": spec.document_key,
                "source_group": source_group,
                "status": "reuse-ok",
            })
            print(f"OK reuse   {spec.document_key}")
            return True
        if spec.local_path is not None:
            local = (primary.parent / spec.local_path).resolve()
            if local.is_file() and local.stat().st_size > 0:
                accepted.append(spec)
                probe_rows.append({"document_key": spec.document_key, "source_group": source_group, "status": "local-ok"})
                print(f"OK local   {spec.document_key}")
                return True
            error = f"local source missing: {local}"
        else:
            try:
                detail = _probe_remote(spec, timeout_s=timeout_s)
            except Exception as exc:
                error = str(exc)
            else:
                accepted.append(spec)
                probe_rows.append({"document_key": spec.document_key, "source_group": source_group, **detail})
                print(f"OK remote  {spec.document_key}")
                return True
        rejected.append({
            "document_key": spec.document_key,
            "source_group": source_group,
            "url": spec.url,
            "filename": spec.filename,
            "error": error,
        })
        print(f"REJECT     {spec.document_key}: {error}")
        return False

    for spec in primary_specs:
        process(spec, "primary")

    if len(accepted) < target_count:
        for spec in fallback_specs:
            if len(accepted) >= target_count:
                break
            process(spec, "fallback")

    output_dir.mkdir(parents=True, exist_ok=True)
    accepted_path = output_dir / "resolved_source_seed.jsonl"
    rejected_path = output_dir / "rejected_sources.jsonl"
    probes_path = output_dir / "preflight_results.jsonl"

    with accepted_path.open("w", encoding="utf-8") as file:
        for spec in accepted:
            json.dump(_spec_to_json(spec), file, ensure_ascii=False)
            file.write("\n")
    with rejected_path.open("w", encoding="utf-8") as file:
        for row in rejected:
            json.dump(row, file, ensure_ascii=False)
            file.write("\n")
    with probes_path.open("w", encoding="utf-8") as file:
        for row in probe_rows:
            json.dump(row, file, ensure_ascii=False)
            file.write("\n")

    summary = {
        "preflight_version": PREFLIGHT_SCRIPT_VERSION,
        "acquire_version": ACQUIRE_SCRIPT_VERSION,
        "primary_count": len(primary_specs),
        "fallback_available_count": len(fallback_specs),
        "target_count": target_count,
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "target_met": len(accepted) >= target_count,
        "resolved_seed_path": str(accepted_path),
        "rejected_sources_path": str(rejected_path),
        "preflight_results_path": str(probes_path),
    }
    (output_dir / "preflight_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not summary["target_met"]:
        raise EvaluationContractError(
            f"preflight target not met: accepted={len(accepted)} target={target_count}"
        )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preflight corpus sources before a long build.")
    parser.add_argument("--version", action="version", version=PREFLIGHT_SCRIPT_VERSION)
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--fallback", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-count", type=int, default=30)
    parser.add_argument("--reuse-corpus-root", type=Path)
    parser.add_argument("--timeout-s", type=float, default=20.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_preflight(
        primary=args.primary,
        fallbacks=args.fallback,
        output_dir=args.output_dir,
        target_count=args.target_count,
        reuse_corpus_root=args.reuse_corpus_root,
        timeout_s=args.timeout_s,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
