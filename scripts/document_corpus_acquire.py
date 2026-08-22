from __future__ import annotations

import argparse
import json
import re
import shutil
from urllib.parse import urlparse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
import sys

# Support both:
#   python scripts/document_corpus_acquire.py ...
#   python -m scripts.document_corpus_acquire ...
# Direct script execution puts scripts/ rather than the repository root on
# sys.path, so add the repository root before importing sibling modules via
# the scripts namespace.
if __package__ in (None, ""):
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

import httpx
from pypdf import PdfReader

from scripts.document_corpus_contract import (
    CanonicalDocumentUnit,
    CorpusDocument,
    CorpusDocumentOrigin,
    CorpusManifest,
    sha256_file,
)
from scripts.document_retrieval_dataset import DocumentSourceMode
from scripts.eval_contract import EvaluationContractError

ACQUIRE_SCRIPT_VERSION = "phase-b2-v4"


@dataclass(frozen=True, slots=True)
class CorpusSourceSpec:
    document_key: str
    workspace_key: str
    filename: str
    title: str
    topic: str
    url: str | None = None
    local_path: str | None = None
    source_mode: DocumentSourceMode = DocumentSourceMode.NATIVE_TEXT
    origin: CorpusDocumentOrigin = CorpusDocumentOrigin.REAL
    variant_of: str | None = None
    publication_date: str | None = None
    license_note: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CorpusSourceSpec":
        try:
            source_mode = DocumentSourceMode(
                str(data.get("source_mode", "native_text")).strip()
            )
        except ValueError as exc:
            raise EvaluationContractError(
                f"invalid source_mode: {data.get('source_mode')!r}"
            ) from exc
        try:
            origin = CorpusDocumentOrigin(
                str(data.get("origin", "real")).strip()
            )
        except ValueError as exc:
            raise EvaluationContractError(
                f"invalid origin: {data.get('origin')!r}"
            ) from exc

        item = cls(
            document_key=str(data.get("document_key", "")).strip(),
            workspace_key=str(data.get("workspace_key", "")).strip(),
            filename=str(data.get("filename", "")).strip(),
            title=str(data.get("title", "")).strip(),
            topic=str(data.get("topic", "")).strip(),
            url=(str(data["url"]).strip() if data.get("url") else None),
            local_path=(
                str(data["local_path"]).strip()
                if data.get("local_path")
                else None
            ),
            source_mode=source_mode,
            origin=origin,
            variant_of=(
                str(data["variant_of"]).strip()
                if data.get("variant_of") is not None
                else None
            ),
            publication_date=(
                str(data["publication_date"]).strip()
                if data.get("publication_date") is not None
                else None
            ),
            license_note=(
                str(data["license_note"]).strip()
                if data.get("license_note") is not None
                else None
            ),
        )
        item.validate()
        return item

    def validate(self) -> None:
        if not self.document_key:
            raise EvaluationContractError("document_key must not be empty")
        if not self.workspace_key:
            raise EvaluationContractError(
                f"workspace_key must not be empty: {self.document_key}"
            )
        if not self.filename or Path(self.filename).name != self.filename:
            raise EvaluationContractError(
                f"filename must be a basename: {self.document_key}"
            )
        if Path(self.filename).suffix.lower() not in {".pdf", ".md", ".txt"}:
            raise EvaluationContractError(
                f"unsupported source extension: {self.filename}"
            )
        if not self.title:
            raise EvaluationContractError(
                f"title must not be empty: {self.document_key}"
            )
        if not self.topic:
            raise EvaluationContractError(
                f"topic must not be empty: {self.document_key}"
            )
        provided = int(self.url is not None) + int(self.local_path is not None)
        if provided != 1:
            raise EvaluationContractError(
                f"exactly one of url/local_path is required: {self.document_key}"
            )
        if self.url is not None and not self.url.startswith("https://"):
            raise EvaluationContractError(
                f"remote corpus URL must use https: {self.document_key}"
            )
        if self.origin is CorpusDocumentOrigin.DERIVED and not self.variant_of:
            raise EvaluationContractError(
                f"derived source requires variant_of: {self.document_key}"
            )
        if self.origin is CorpusDocumentOrigin.REAL and self.variant_of is not None:
            raise EvaluationContractError(
                f"real source cannot declare variant_of: {self.document_key}"
            )


class CorpusBuildIncompleteError(EvaluationContractError):
    """Raised after a corpus build records one or more source failures."""

    def __init__(self, summary: dict[str, Any]) -> None:
        self.summary = summary
        super().__init__(
            "corpus build incomplete: "
            f"{summary.get('failed_source_count', 0)} source(s) failed"
        )


def _looks_like_html(payload: bytes) -> bool:
    sample = payload[:4096].lstrip().lower()
    return (
        sample.startswith(b"<!doctype html")
        or sample.startswith(b"<html")
        or b"<html" in sample[:512]
    )


def validate_downloaded_bytes(*, spec: CorpusSourceSpec, payload: bytes) -> None:
    """Reject anti-bot/error HTML before it can masquerade as corpus content."""
    if not payload:
        raise EvaluationContractError(
            f"empty download: {spec.document_key}: {spec.url}"
        )

    suffix = Path(spec.filename).suffix.lower()
    if suffix == ".pdf" and not payload.lstrip().startswith(b"%PDF-"):
        hint = "html-response" if _looks_like_html(payload) else "non-pdf-response"
        raise EvaluationContractError(
            f"downloaded payload is not a PDF ({hint}): "
            f"{spec.document_key}: {spec.url}"
        )
    if suffix in {".md", ".txt"} and _looks_like_html(payload):
        raise EvaluationContractError(
            f"downloaded text source is HTML instead of source content: "
            f"{spec.document_key}: {spec.url}"
        )


def load_source_specs(path: Path) -> list[CorpusSourceSpec]:
    if not path.is_file():
        raise FileNotFoundError(f"source spec not found: {path}")
    specs: list[CorpusSourceSpec] = []
    seen_keys: set[str] = set()
    seen_filenames: set[str] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationContractError(
                f"invalid JSON at {path}:{line_number}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise EvaluationContractError(
                f"source spec must be object at {path}:{line_number}"
            )
        spec = CorpusSourceSpec.from_dict(payload)
        if spec.document_key in seen_keys:
            raise EvaluationContractError(
                f"duplicate document_key: {spec.document_key}"
            )
        if spec.filename in seen_filenames:
            raise EvaluationContractError(
                f"duplicate filename: {spec.filename}"
            )
        seen_keys.add(spec.document_key)
        seen_filenames.add(spec.filename)
        specs.append(spec)
    if not specs:
        raise EvaluationContractError("source spec contains no documents")
    return specs


def download_bytes(url: str, *, timeout_s: float = 60.0) -> bytes:
    """Download with ordinary browser-compatible headers.

    Some government media endpoints (notably FDA) redirect non-browser user
    agents to anti-abuse pages.  We still validate the returned bytes
    separately; headers are compatibility, not trust.
    """
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

    with httpx.Client(
        timeout=httpx.Timeout(timeout_s),
        follow_redirects=True,
        headers=headers,
    ) as client:
        response = client.get(url)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise EvaluationContractError(
                "remote source download failed: "
                f"status={response.status_code} requested={url} final={response.url}"
            ) from exc
        if not response.content:
            raise EvaluationContractError(f"empty download: {url}")
        return response.content


def materialize_source(
    *,
    spec: CorpusSourceSpec,
    destination: Path,
    source_spec_root: Path,
    downloader: Callable[[str], bytes] = download_bytes,
    refresh: bool = False,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if spec.url is not None and destination.is_file() and destination.stat().st_size > 0 and not refresh:
        return
    if spec.local_path is not None:
        local = (source_spec_root / spec.local_path).resolve()
        if not local.is_file():
            raise EvaluationContractError(
                f"local source does not exist: {spec.document_key}: {local}"
            )
        shutil.copyfile(local, destination)
        return
    assert spec.url is not None
    payload = downloader(spec.url)
    validate_downloaded_bytes(spec=spec, payload=payload)
    destination.write_bytes(payload)


def _pdf_units(*, document_key: str, source_path: Path) -> list[CanonicalDocumentUnit]:
    try:
        reader = PdfReader(str(source_path))
    except Exception as exc:
        raise EvaluationContractError(
            f"unable to read PDF for canonical ground truth: {document_key}"
        ) from exc
    if reader.is_encrypted:
        raise EvaluationContractError(
            f"encrypted PDF cannot be canonical corpus source: {document_key}"
        )
    units: list[CanonicalDocumentUnit] = []
    for page_number, page in enumerate(reader.pages, 1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception as exc:
            raise EvaluationContractError(
                f"canonical PDF text extraction failed: {document_key}: page {page_number}"
            ) from exc
        if text:
            units.append(
                CanonicalDocumentUnit(
                    document_key=document_key,
                    page=page_number,
                    section=None,
                    text=text,
                )
            )
    if not units:
        raise EvaluationContractError(
            f"native canonical PDF contains no extractable text: {document_key}"
        )
    return units


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _markdown_units(
    *, document_key: str, source_path: Path
) -> list[CanonicalDocumentUnit]:
    lines = source_path.read_text(encoding="utf-8").splitlines()
    units: list[CanonicalDocumentUnit] = []
    current_section = "__preamble__"
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            units.append(
                CanonicalDocumentUnit(
                    document_key=document_key,
                    page=None,
                    section=current_section,
                    text=text,
                )
            )
        buffer.clear()

    for line in lines:
        match = _HEADING.match(line)
        if match:
            flush()
            current_section = match.group(2).strip()
            continue
        buffer.append(line)
    flush()
    if not units:
        raise EvaluationContractError(
            f"markdown canonical source contains no text: {document_key}"
        )
    return units


def _text_units(*, document_key: str, source_path: Path) -> list[CanonicalDocumentUnit]:
    text = source_path.read_text(encoding="utf-8").strip()
    if not text:
        raise EvaluationContractError(
            f"text canonical source contains no text: {document_key}"
        )
    return [
        CanonicalDocumentUnit(
            document_key=document_key,
            page=None,
            section="document",
            text=text,
        )
    ]


def canonical_units_for_source(
    *, spec: CorpusSourceSpec, source_path: Path
) -> list[CanonicalDocumentUnit]:
    suffix = source_path.suffix.lower()
    if suffix == ".pdf":
        return _pdf_units(document_key=spec.document_key, source_path=source_path)
    if suffix == ".md":
        return _markdown_units(document_key=spec.document_key, source_path=source_path)
    if suffix == ".txt":
        return _text_units(document_key=spec.document_key, source_path=source_path)
    raise EvaluationContractError(f"unsupported canonical source: {source_path}")



def reuse_frozen_sources(
    *,
    previous_corpus_root: Path,
    specs: list[CorpusSourceSpec],
    output_root: Path,
) -> dict[str, int]:
    """Reuse source bytes only when the previous resolved source identity matches.

    This preserves a frozen earlier corpus while avoiding redundant downloads when
    a later corpus version is a strict source superset. Canonical units and the
    new manifest are always rebuilt in the new output root.
    """
    resolved_path = previous_corpus_root / "resolved_sources.jsonl"
    previous_sources_dir = previous_corpus_root / "sources"
    if not resolved_path.is_file() or not previous_sources_dir.is_dir():
        raise EvaluationContractError(
            f"reuse corpus is missing resolved_sources.jsonl or sources/: {previous_corpus_root}"
        )

    previous_rows: dict[str, dict[str, Any]] = {}
    for line_number, raw in enumerate(resolved_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise EvaluationContractError(
                f"invalid reuse resolved source at {resolved_path}:{line_number}"
            )
        previous_rows[str(payload.get("document_key", ""))] = payload

    destination_dir = output_root / "sources"
    destination_dir.mkdir(parents=True, exist_ok=True)
    reused = 0
    skipped = 0
    for spec in specs:
        previous = previous_rows.get(spec.document_key)
        if previous is None:
            skipped += 1
            continue
        identity_matches = (
            str(previous.get("filename", "")) == spec.filename
            and previous.get("url") == spec.url
            and previous.get("local_path") == spec.local_path
        )
        previous_source = previous_sources_dir / spec.filename
        if not identity_matches or not previous_source.is_file() or previous_source.stat().st_size <= 0:
            skipped += 1
            continue
        destination = destination_dir / spec.filename
        shutil.copyfile(previous_source, destination)
        reused += 1
    return {"reused_source_count": reused, "reuse_skipped_count": skipped}

def build_corpus(
    *,
    source_spec_path: Path,
    output_root: Path,
    corpus_id: str,
    corpus_version: str,
    downloader: Callable[[str], bytes] = download_bytes,
    refresh: bool = False,
    reuse_corpus_root: Path | None = None,
    allow_partial: bool = False,
) -> dict[str, Any]:
    specs = load_source_specs(source_spec_path)
    output_root.mkdir(parents=True, exist_ok=True)
    reuse_summary = {"reused_source_count": 0, "reuse_skipped_count": 0}
    if reuse_corpus_root is not None and not refresh:
        reuse_summary = reuse_frozen_sources(
            previous_corpus_root=reuse_corpus_root,
            specs=specs,
            output_root=output_root,
        )
    sources_dir = output_root / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    documents: list[CorpusDocument] = []
    units: list[CanonicalDocumentUnit] = []
    resolved_rows: list[dict[str, Any]] = []
    failure_rows: list[dict[str, Any]] = []

    for index, spec in enumerate(specs, start=1):
        source_path = sources_dir / spec.filename
        print(f"[{index}/{len(specs)}] materialize {spec.document_key}")
        try:
            materialize_source(
                spec=spec,
                destination=source_path,
                source_spec_root=source_spec_path.parent,
                downloader=downloader,
                refresh=refresh,
            )
            source_sha = sha256_file(source_path)
            source_units = canonical_units_for_source(spec=spec, source_path=source_path)
        except Exception as exc:
            # A failed remote source must not erase progress for the remaining
            # corpus.  Remove a newly-invalid destination, record the failure,
            # then continue.  Completion is still fail-closed below.
            if source_path.is_file():
                try:
                    validate_downloaded_bytes(spec=spec, payload=source_path.read_bytes())
                except EvaluationContractError:
                    source_path.unlink(missing_ok=True)
            failure = {
                "index": index,
                "document_key": spec.document_key,
                "filename": spec.filename,
                "url": spec.url,
                "local_path": spec.local_path,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            failure_rows.append(failure)
            print(
                f"[{index}/{len(specs)}] FAILED {spec.document_key}: "
                f"{type(exc).__name__}: {exc}"
            )
            continue

        units.extend(source_units)
        documents.append(
            CorpusDocument(
                document_key=spec.document_key,
                workspace_key=spec.workspace_key,
                source_path=str(source_path.relative_to(output_root)),
                source_sha256=source_sha,
                source_mode=spec.source_mode,
                origin=spec.origin,
                variant_of=spec.variant_of,
                source_url=spec.url,
            )
        )
        resolved_rows.append(
            {
                **asdict(spec),
                "source_mode": spec.source_mode.value,
                "origin": spec.origin.value,
                "source_sha256": source_sha,
                "canonical_unit_count": len(source_units),
                "canonical_character_count": sum(len(unit.text) for unit in source_units),
            }
        )

    units_path = output_root / "canonical_units.jsonl"
    with units_path.open("w", encoding="utf-8") as file:
        for unit in units:
            json.dump(asdict(unit), file, ensure_ascii=False)
            file.write("\n")

    manifest = CorpusManifest(
        corpus_id=corpus_id.strip(),
        corpus_version=corpus_version.strip(),
        documents=tuple(documents),
        canonical_units_path=units_path.name,
        canonical_units_sha256=sha256_file(units_path),
    )
    manifest.validate()
    manifest_path = output_root / "corpus_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    resolved_path = output_root / "resolved_sources.jsonl"
    with resolved_path.open("w", encoding="utf-8") as file:
        for row in resolved_rows:
            json.dump(row, file, ensure_ascii=False)
            file.write("\n")

    failures_path = output_root / "corpus_build_failures.jsonl"
    with failures_path.open("w", encoding="utf-8") as file:
        for row in failure_rows:
            json.dump(row, file, ensure_ascii=False)
            file.write("\n")

    summary = {
        "corpus_id": manifest.corpus_id,
        "corpus_version": manifest.corpus_version,
        "requested_document_count": len(specs),
        "document_count": len(documents),
        "failed_source_count": len(failure_rows),
        "complete": not failure_rows,
        "failures_path": str(failures_path),
        "workspace_count": len({item.workspace_key for item in documents}),
        "canonical_unit_count": len(units),
        "canonical_character_count": sum(len(unit.text) for unit in units),
        "manifest_path": str(manifest_path),
        "canonical_units_path": str(units_path),
        "resolved_sources_path": str(resolved_path),
        "manifest_sha256": sha256_file(manifest_path),
        **reuse_summary,
    }
    (output_root / "corpus_build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if failure_rows and not allow_partial:
        raise CorpusBuildIncompleteError(summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Acquire a reproducible real-document corpus and build canonical ground truth."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=ACQUIRE_SCRIPT_VERSION,
    )
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--corpus-id", required=True)
    parser.add_argument("--corpus-version", required=True)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="redownload remote files even when a local frozen copy already exists",
    )
    parser.add_argument(
        "--reuse-corpus-root",
        type=Path,
        help="reuse matching frozen source bytes from an earlier corpus root",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "accept a corpus with source failures; final benchmark builds should "
            "leave this disabled"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        summary = build_corpus(
            source_spec_path=args.sources,
            output_root=args.output_root,
            corpus_id=args.corpus_id,
            corpus_version=args.corpus_version,
            refresh=args.refresh,
            reuse_corpus_root=args.reuse_corpus_root,
            allow_partial=args.allow_partial,
        )
    except CorpusBuildIncompleteError as exc:
        print(json.dumps(exc.summary, ensure_ascii=False, indent=2))
        print(
            "Corpus build is incomplete; inspect failures_path and rerun. "
            "Use --allow-partial only for diagnostics.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
