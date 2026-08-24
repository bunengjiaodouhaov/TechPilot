from __future__ import annotations

import asyncio
import json
import shutil
import stat
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.answering.llm import SYSTEM_PROMPT, build_user_prompt
from app.api.dependencies import get_embedding_provider, get_llm_provider
from app.repository.code_hybrid import CodeHybridRetrievalService
from app.repository.code_index import InMemoryCodeDenseIndex, InMemoryCodeKeywordIndex
from app.repository.code_retrieval import CodeIndexBuildReport, CodeIndexingService, CodeRetrievalService
from app.repository.read_boundary import RepositoryReadBoundary

router = APIRouter(prefix="/repository", tags=["repository-product"])

_REPOSITORY_STORE = Path(".local/repositories")
_MAX_ZIP_BYTES = 50 * 1024 * 1024
_MAX_EXTRACTED_BYTES = 120 * 1024 * 1024
_MAX_FILES = 5000
_MANIFEST = "techpilot_repository.json"


class RepositorySummary(BaseModel):
    repository_id: str
    name: str
    builtin: bool
    persisted: bool


class RepositoryEvidence(BaseModel):
    source_id: str
    file_path: str
    symbol: str
    kind: str
    line_start: int
    line_end: int
    score: float
    excerpt: str


class RepositoryQueryRequest(BaseModel):
    repository_id: str = Field(default="techpilot", min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=10)


class RepositoryQueryResponse(BaseModel):
    repository_id: str
    repository: str
    answer: str
    refused: bool
    citations: list[RepositoryEvidence]
    indexed_files: int
    indexed_chunks: int


@dataclass
class _RepositoryRuntime:
    repository_id: str
    boundary: RepositoryReadBoundary
    keyword: InMemoryCodeKeywordIndex
    dense: InMemoryCodeDenseIndex
    retrieval: CodeRetrievalService
    hybrid: CodeHybridRetrievalService
    report: CodeIndexBuildReport


_runtimes: dict[str, _RepositoryRuntime] = {}
_runtime_lock = asyncio.Lock()


def _builtin_root() -> Path:
    return Path.cwd().resolve()


def _store_root() -> Path:
    root = _REPOSITORY_STORE.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _manifest_for(directory: Path) -> dict[str, object] | None:
    path = directory / _MANIFEST
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _uploaded_root(repository_id: str) -> Path:
    if not repository_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in repository_id):
        raise HTTPException(status_code=404, detail="repository not found")
    base = _store_root()
    candidate = (base / repository_id).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="repository not found") from exc
    source = candidate / "source"
    if not source.is_dir() or _manifest_for(candidate) is None:
        raise HTTPException(status_code=404, detail="repository not found")
    return source


def _repository_root(repository_id: str) -> Path:
    if repository_id == "techpilot":
        return _builtin_root()
    return _uploaded_root(repository_id)


def _repository_name(repository_id: str) -> str:
    if repository_id == "techpilot":
        return _builtin_root().name
    directory = _store_root() / repository_id
    manifest = _manifest_for(directory) or {}
    return str(manifest.get("name") or repository_id)


async def _build_runtime(repository_id: str) -> _RepositoryRuntime:
    boundary = RepositoryReadBoundary(_repository_root(repository_id))
    keyword = InMemoryCodeKeywordIndex()
    dense = InMemoryCodeDenseIndex()
    retrieval = CodeRetrievalService(
        embedding_provider=get_embedding_provider(),
        keyword_index=keyword,
        dense_index=dense,
    )
    indexing = CodeIndexingService(
        boundary=boundary,
        embedding_provider=get_embedding_provider(),
        keyword_index=keyword,
        dense_index=dense,
    )
    report = await indexing.rebuild()
    return _RepositoryRuntime(
        repository_id=repository_id,
        boundary=boundary,
        keyword=keyword,
        dense=dense,
        retrieval=retrieval,
        hybrid=CodeHybridRetrievalService(retrieval_service=retrieval),
        report=report,
    )


async def _get_runtime(
    repository_id: str,
    *,
    rebuild: bool = False,
) -> _RepositoryRuntime:
    async with _runtime_lock:
        runtime = _runtimes.get(repository_id)
        if rebuild or runtime is None:
            runtime = await _build_runtime(repository_id)
            _runtimes[repository_id] = runtime
        return runtime


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members: list[zipfile.ZipInfo] = []
    total_size = 0

    for info in archive.infolist():
        path = PurePosixPath(info.filename.replace("\\", "/"))
        if not info.filename or info.is_dir():
            continue
        if path.is_absolute() or ".." in path.parts:
            raise HTTPException(status_code=422, detail="unsafe archive path")
        if len(members) >= _MAX_FILES:
            raise HTTPException(status_code=413, detail="repository contains too many files")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise HTTPException(status_code=422, detail="repository archive contains symlink")
        total_size += int(info.file_size)
        if total_size > _MAX_EXTRACTED_BYTES:
            raise HTTPException(status_code=413, detail="repository expands beyond size limit")
        members.append(info)

    if not members:
        raise HTTPException(status_code=422, detail="repository archive is empty")
    return members


def _extract_archive(archive_path: Path, target: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        members = _safe_members(archive)
        for info in members:
            relative = PurePosixPath(info.filename.replace("\\", "/"))
            destination = target.joinpath(*relative.parts).resolve()
            try:
                destination.relative_to(target.resolve())
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="unsafe archive path") from exc
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _collapse_single_root(source: Path) -> None:
    visible = [item for item in source.iterdir() if item.name != "__MACOSX"]
    if len(visible) != 1 or not visible[0].is_dir():
        return
    child = visible[0]
    temporary = source.parent / "_collapsed"
    if temporary.exists():
        shutil.rmtree(temporary)
    child.rename(temporary)
    source.rmdir()
    temporary.rename(source)


def _excerpt(
    boundary: RepositoryReadBoundary,
    file_path: str,
    line_start: int,
    line_end: int,
) -> str:
    resolved = boundary.resolve_file(file_path)
    lines = resolved.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[line_start - 1 : line_end])


@router.get("/repositories", response_model=list[RepositorySummary])
async def list_repositories() -> list[RepositorySummary]:
    results = [
        RepositorySummary(
            repository_id="techpilot",
            name=_builtin_root().name,
            builtin=True,
            persisted=True,
        )
    ]
    for directory in sorted(_store_root().iterdir()):
        if not directory.is_dir():
            continue
        manifest = _manifest_for(directory)
        if not manifest or not (directory / "source").is_dir():
            continue
        results.append(
            RepositorySummary(
                repository_id=directory.name,
                name=str(manifest.get("name") or directory.name),
                builtin=False,
                persisted=True,
            )
        )
    return results


@router.post(
    "/repositories/upload",
    response_model=RepositorySummary,
    status_code=status.HTTP_201_CREATED,
)
async def upload_repository(
    file: UploadFile = File(...),
) -> RepositorySummary:
    filename = file.filename or ""
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=415, detail="upload a .zip repository archive")

    content = await file.read(_MAX_ZIP_BYTES + 1)
    await file.close()
    if len(content) > _MAX_ZIP_BYTES:
        raise HTTPException(status_code=413, detail="repository zip exceeds 50 MB")

    repository_id = uuid.uuid4().hex[:12]
    directory = _store_root() / repository_id
    source = directory / "source"
    archive_path = directory / "upload.zip"
    directory.mkdir(parents=True, exist_ok=False)
    source.mkdir()

    try:
        archive_path.write_bytes(content)
        try:
            _extract_archive(archive_path, source)
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=422, detail="invalid zip archive") from exc
        _collapse_single_root(source)
        # Validate the resulting root through the existing repository boundary.
        boundary = RepositoryReadBoundary(source)
        if not any(path.endswith(".py") for path in boundary.list_files()):
            raise HTTPException(status_code=422, detail="repository contains no readable Python files")
        name = Path(filename).stem.strip() or f"repository-{repository_id}"
        (directory / _MANIFEST).write_text(
            json.dumps(
                {"repository_id": repository_id, "name": name},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        archive_path.unlink(missing_ok=True)

    return RepositorySummary(
        repository_id=repository_id,
        name=name,
        builtin=False,
        persisted=True,
    )


@router.delete(
    "/repositories/{repository_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_repository(repository_id: str) -> None:
    if repository_id == "techpilot":
        raise HTTPException(status_code=409, detail="built-in TechPilot repository cannot be deleted")
    source = _uploaded_root(repository_id)
    directory = source.parent
    _runtimes.pop(repository_id, None)
    shutil.rmtree(directory)


@router.get("/status")
async def repository_status(repository_id: str = "techpilot") -> dict[str, object]:
    runtime = await _get_runtime(repository_id)
    return {
        "repository_id": repository_id,
        "repository": _repository_name(repository_id),
        "python_files": runtime.report.python_file_count,
        "chunks": runtime.report.chunk_count,
        "parse_errors": runtime.report.parse_error_count,
        "read_errors": runtime.report.read_error_count,
    }


@router.post("/reindex")
async def repository_reindex(repository_id: str = "techpilot") -> dict[str, object]:
    runtime = await _get_runtime(repository_id, rebuild=True)
    return {
        "repository_id": repository_id,
        "repository": _repository_name(repository_id),
        "python_files": runtime.report.python_file_count,
        "chunks": runtime.report.chunk_count,
    }


@router.post("/query", response_model=RepositoryQueryResponse)
async def repository_query(request: RepositoryQueryRequest) -> RepositoryQueryResponse:
    question = request.question.strip()
    runtime = await _get_runtime(request.repository_id)
    hits = await runtime.hybrid.search(query=question, limit=request.limit)

    if not hits:
        return RepositoryQueryResponse(
            repository_id=request.repository_id,
            repository=_repository_name(request.repository_id),
            answer="现有代码索引中没有足够证据回答这个问题。",
            refused=True,
            citations=[],
            indexed_files=runtime.report.python_file_count,
            indexed_chunks=runtime.report.chunk_count,
        )

    evidence: list[RepositoryEvidence] = []
    prompt_sources: list[str] = []
    for index, hit in enumerate(hits, start=1):
        source_id = f"SOURCE_{index}"
        excerpt = _excerpt(runtime.boundary, hit.file_path, hit.line_start, hit.line_end)
        evidence.append(
            RepositoryEvidence(
                source_id=source_id,
                file_path=hit.file_path,
                symbol=hit.symbol,
                kind=hit.kind.value,
                line_start=hit.line_start,
                line_end=hit.line_end,
                score=hit.score,
                excerpt=excerpt,
            )
        )
        prompt_sources.append(
            "\n".join(
                [
                    source_id,
                    f"path: {hit.file_path}",
                    f"symbol: {hit.symbol}",
                    f"lines: {hit.line_start}-{hit.line_end}",
                    "code:",
                    excerpt,
                ]
            )
        )

    llm_answer = await get_llm_provider().generate(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=build_user_prompt(
            question=question,
            prompt_context="\n\n".join(prompt_sources),
        ),
    )
    cited = set(llm_answer.cited_source_ids)

    return RepositoryQueryResponse(
        repository_id=request.repository_id,
        repository=_repository_name(request.repository_id),
        answer=llm_answer.text,
        refused=llm_answer.refused,
        citations=[item for item in evidence if item.source_id in cited],
        indexed_files=runtime.report.python_file_count,
        indexed_chunks=runtime.report.chunk_count,
    )
