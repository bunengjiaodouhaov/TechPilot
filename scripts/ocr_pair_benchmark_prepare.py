from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter


SCRIPT_VERSION = "ocr-paired-benchmark-prepare-v1"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        1,
    ):
        if not raw.strip():
            continue
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise ValueError(f"expected object at {path}:{line_number}")
        rows.append(row)
    if not rows:
        raise ValueError(f"empty JSONL: {path}")
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_single_native_page(
    *,
    source_path: Path,
    page_number: int,
    destination: Path,
) -> str:
    reader = PdfReader(str(source_path))
    if reader.is_encrypted:
        raise ValueError(f"encrypted PDF: {source_path}")
    if page_number <= 0 or page_number > len(reader.pages):
        raise ValueError(
            f"page {page_number} outside 1..{len(reader.pages)}: {source_path}"
        )

    page = reader.pages[page_number - 1]
    canonical_text = (page.extract_text() or "").strip()
    if not canonical_text:
        raise ValueError(
            f"source page has no native text: {source_path} page={page_number}"
        )

    writer = PdfWriter()
    writer.add_page(page)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as file:
        writer.write(file)

    return canonical_text


def write_scanned_page(
    *,
    source_path: Path,
    page_number: int,
    destination: Path,
    dpi: int,
) -> None:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError(
            "pypdfium2 is required; run pip install -r requirements.txt"
        ) from exc

    document = pdfium.PdfDocument(str(source_path))
    page = document[page_number - 1]
    bitmap = page.render(scale=dpi / 72.0)
    image = bitmap.to_pil().convert("RGB")

    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(
        destination,
        "PDF",
        resolution=float(dpi),
    )

    # Image-only PDF must not accidentally preserve a text layer.
    scan_reader = PdfReader(str(destination))
    extracted = " ".join(
        (item.extract_text() or "").strip()
        for item in scan_reader.pages
    ).strip()
    alnum_count = sum(char.isalnum() for char in extracted)
    if alnum_count >= 24:
        raise RuntimeError(
            f"generated scanned PDF unexpectedly contains native text: "
            f"{destination} alnum={alnum_count}"
        )


def choose_groups(
    *,
    cases: list[dict[str, Any]],
    manifest: dict[str, Any],
    target_pages: int,
    queries_per_page: int,
) -> list[tuple[str, int, list[dict[str, Any]]]]:
    docs = {
        str(item["document_key"]): item
        for item in manifest.get("documents", [])
    }

    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        document_key = str(case.get("document_key", "")).strip()
        page = case.get("page")
        evidence = str(case.get("evidence_quote", "")).strip()
        query = str(case.get("query", "")).strip()

        if (
            document_key not in docs
            or not isinstance(page, int)
            or page <= 0
            or not evidence
            or not query
        ):
            continue

        source_path = str(docs[document_key].get("source_path", ""))
        if Path(source_path).suffix.lower() != ".pdf":
            continue

        grouped[(document_key, page)].append(case)

    eligible_by_doc: dict[
        str,
        list[tuple[str, int, list[dict[str, Any]]]],
    ] = defaultdict(list)

    for (document_key, page), rows in grouped.items():
        if len(rows) < queries_per_page:
            continue
        selected_rows = sorted(
            rows,
            key=lambda row: stable_key(str(row["candidate_id"])),
        )[:queries_per_page]
        eligible_by_doc[document_key].append(
            (document_key, page, selected_rows)
        )

    for document_key, rows in eligible_by_doc.items():
        rows.sort(
            key=lambda item: stable_key(
                f"{item[0]}::{item[1]}"
            )
        )

    doc_order = sorted(
        eligible_by_doc,
        key=stable_key,
    )

    selected: list[tuple[str, int, list[dict[str, Any]]]] = []
    while len(selected) < target_pages:
        progressed = False
        for document_key in doc_order:
            rows = eligible_by_doc[document_key]
            if not rows:
                continue
            selected.append(rows.pop(0))
            progressed = True
            if len(selected) >= target_pages:
                break
        if not progressed:
            break

    if len(selected) < target_pages:
        raise RuntimeError(
            f"only {len(selected)} eligible pages found; "
            f"target_pages={target_pages}"
        )

    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-pages", type=int, default=20)
    parser.add_argument("--queries-per-page", type=int, default=2)
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args()

    if args.target_pages <= 0:
        raise ValueError("target-pages must be positive")
    if args.queries_per_page <= 0:
        raise ValueError("queries-per-page must be positive")
    if args.dpi <= 0:
        raise ValueError("dpi must be positive")

    manifest_path = args.corpus_root / "corpus_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = load_jsonl(args.queries)

    selected = choose_groups(
        cases=cases,
        manifest=manifest,
        target_pages=args.target_pages,
        queries_per_page=args.queries_per_page,
    )

    manifest_docs = {
        str(item["document_key"]): item
        for item in manifest["documents"]
    }

    native_dir = args.output_dir / "native"
    scanned_dir = args.output_dir / "scanned"
    native_dir.mkdir(parents=True, exist_ok=True)
    scanned_dir.mkdir(parents=True, exist_ok=True)

    pair_rows: list[dict[str, Any]] = []
    benchmark_cases: list[dict[str, Any]] = []

    for index, (document_key, page, query_rows) in enumerate(
        selected,
        1,
    ):
        pair_id = f"ocr-pair-{index:03d}"
        source_rel = Path(
            str(manifest_docs[document_key]["source_path"])
        )
        source_path = (args.corpus_root / source_rel).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)

        native_name = f"{pair_id}-native.pdf"
        scanned_name = f"{pair_id}-scanned.pdf"
        native_path = native_dir / native_name
        scanned_path = scanned_dir / scanned_name

        canonical_text = write_single_native_page(
            source_path=source_path,
            page_number=page,
            destination=native_path,
        )
        write_scanned_page(
            source_path=source_path,
            page_number=page,
            destination=scanned_path,
            dpi=args.dpi,
        )

        pair_rows.append(
            {
                "pair_id": pair_id,
                "source_document_key": document_key,
                "source_page": page,
                "source_path": str(source_rel),
                "native_filename": native_name,
                "scanned_filename": scanned_name,
                "native_sha256": sha256_file(native_path),
                "scanned_sha256": sha256_file(scanned_path),
                "canonical_text_sha256": hashlib.sha256(
                    canonical_text.encode("utf-8")
                ).hexdigest(),
                "query_count": len(query_rows),
            }
        )

        for query_row in query_rows:
            benchmark_cases.append(
                {
                    "candidate_id": str(query_row["candidate_id"]),
                    "pair_id": pair_id,
                    "query": str(query_row["query"]),
                    "category": str(query_row["category"]),
                    "topic": str(query_row.get("topic", "")),
                    "evidence_quote": str(query_row["evidence_quote"]),
                    "native_document_name": native_name,
                    "scanned_document_name": scanned_name,
                    "expected_page": 1,
                    "source_document_key": document_key,
                    "source_page": page,
                    "review_status": "assistant_reviewed_frozen_candidate_set",
                }
            )

        print(
            f"[{index}/{len(selected)}] {document_key} page={page} "
            f"queries={len(query_rows)}"
        )

    pairs_path = args.output_dir / "ocr_pairs.jsonl"
    cases_path = args.output_dir / "ocr_cases.jsonl"

    with pairs_path.open("w", encoding="utf-8") as file:
        for row in pair_rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")

    with cases_path.open("w", encoding="utf-8") as file:
        for row in benchmark_cases:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = {
        "script_version": SCRIPT_VERSION,
        "target_pages": args.target_pages,
        "queries_per_page": args.queries_per_page,
        "pair_count": len(pair_rows),
        "case_count": len(benchmark_cases),
        "source_document_count": len(
            {row["source_document_key"] for row in pair_rows}
        ),
        "dpi": args.dpi,
        "pairs_path": str(pairs_path),
        "cases_path": str(cases_path),
        "native_dir": str(native_dir),
        "scanned_dir": str(scanned_dir),
        "queries_sha256": sha256_file(args.queries),
        "source_manifest_sha256": sha256_file(manifest_path),
    }
    summary_path = args.output_dir / "ocr_prepare_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
