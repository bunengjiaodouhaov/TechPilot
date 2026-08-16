from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.harness.agent_event import (
    AgentEvent,
    AgentEventSink,
    AgentEventType,
    record_event_safely,
)
from app.harness.evidence_pack import (
    EvidenceIssueKind,
    EvidencePack,
    EvidencePackIssue,
)
from app.harness.tool_registry import ToolNotFoundError, ToolRegistry
from app.harness.tool_runtime import ToolResult, ToolRuntime
from app.repository.code_evidence import CodeEvidence


class RepoExploreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    task_intent: str
    search_mode: Literal[
        "code",
        "symbol",
        "both",
        "dense",
        "keyword",
        "hybrid",
        "module",
        "call",
    ] = "both"
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("query", "task_intent")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized


@dataclass(frozen=True)
class _EvidenceCandidate:
    file_path: str
    line_start: int
    line_end: int
    symbol: str | None


class RepoExplorer:
    """Deterministic read-only repository exploration through ToolRuntime."""

    def __init__(
        self,
        *,
        repository: str,
        registry: ToolRegistry,
        runtime: ToolRuntime,
        event_sink: AgentEventSink | None = None,
    ) -> None:
        normalized_repository = repository.strip()
        if not normalized_repository:
            raise ValueError("repository must not be empty")

        self._repository = normalized_repository
        self._registry = registry
        self._runtime = runtime
        self._event_sink = event_sink

    async def explore(
        self,
        request: RepoExploreRequest,
        *,
        trace_metadata: dict[str, Any] | None = None,
    ) -> EvidencePack:
        metadata = dict(trace_metadata or {})
        metadata["trace_id"] = str(
            metadata.get("trace_id") or uuid4().hex
        )
        metadata.setdefault("component", "repo_explorer")

        issues: list[EvidencePackIssue] = []
        candidates: list[_EvidenceCandidate] = []

        if request.search_mode in {"symbol", "both"}:
            result = await self._invoke(
                tool_name="search_symbol",
                arguments={"query": request.query, "limit": request.limit},
                issues=issues,
                trace_metadata=metadata,
            )
            if result is not None and result.ok and result.data is not None:
                parse_error_count = int(result.data.get("parse_error_count", 0))
                if parse_error_count > 0:
                    issues.append(
                        EvidencePackIssue(
                            kind=EvidenceIssueKind.PARSE_ERROR,
                            tool_name="search_symbol",
                            count=parse_error_count,
                        )
                    )

                for match in result.data.get("matches", []):
                    candidates.append(
                        _EvidenceCandidate(
                            file_path=match["path"],
                            line_start=match["line_start"],
                            line_end=match["line_end"],
                            symbol=match["qualified_name"],
                        )
                    )

        if request.search_mode in {"code", "both"}:
            result = await self._invoke(
                tool_name="search_code",
                arguments={"query": request.query, "limit": request.limit},
                issues=issues,
                trace_metadata=metadata,
            )
            if result is not None and result.ok and result.data is not None:
                for match in result.data.get("matches", []):
                    candidates.append(
                        _EvidenceCandidate(
                            file_path=match["path"],
                            line_start=match["line_number"],
                            line_end=match["line_number"],
                            symbol=None,
                        )
                    )

        if request.search_mode in {"dense", "keyword", "hybrid"}:
            tool_name = {
                "dense": "search_code_dense",
                "keyword": "search_code_keyword",
                "hybrid": "search_code_hybrid",
            }[request.search_mode]
            result = await self._invoke(
                tool_name=tool_name,
                arguments={"query": request.query, "limit": request.limit},
                issues=issues,
                trace_metadata=metadata,
            )
            if result is not None and result.ok and result.data is not None:
                for match in result.data.get("matches", []):
                    candidates.append(
                        _EvidenceCandidate(
                            file_path=match["path"],
                            line_start=match["line_start"],
                            line_end=match["line_end"],
                            symbol=match["symbol"],
                        )
                    )

        if request.search_mode == "module":
            result = await self._invoke(
                tool_name="inspect_modules",
                arguments={"query": request.query, "limit": request.limit},
                issues=issues,
                trace_metadata=metadata,
            )
            if result is not None and result.ok and result.data is not None:
                parse_error_count = int(result.data.get("parse_error_count", 0))
                if parse_error_count > 0:
                    issues.append(
                        EvidencePackIssue(
                            kind=EvidenceIssueKind.PARSE_ERROR,
                            tool_name="inspect_modules",
                            count=parse_error_count,
                        )
                    )

                read_error_count = int(result.data.get("read_error_count", 0))
                if read_error_count > 0:
                    issues.append(
                        EvidencePackIssue(
                            kind=EvidenceIssueKind.TOOL_FAILURE,
                            tool_name="inspect_modules",
                            count=read_error_count,
                        )
                    )

                for module in result.data.get("modules", []):
                    module_path = module["path"]

                    for dependency in module.get("internal_dependencies", []):
                        candidates.append(
                            _EvidenceCandidate(
                                file_path=module_path,
                                line_start=dependency["line_start"],
                                line_end=dependency["line_end"],
                                symbol=None,
                            )
                        )

                    for symbol in module.get("symbols", []):
                        candidates.append(
                            _EvidenceCandidate(
                                file_path=module_path,
                                line_start=symbol["line_start"],
                                line_end=symbol["line_start"],
                                symbol=symbol["name"],
                            )
                        )

        if request.search_mode == "call":
            result = await self._invoke(
                tool_name="inspect_calls",
                arguments={"query": request.query, "limit": request.limit},
                issues=issues,
                trace_metadata=metadata,
            )
            if result is not None and result.ok and result.data is not None:
                parse_error_count = int(result.data.get("parse_error_count", 0))
                if parse_error_count > 0:
                    issues.append(
                        EvidencePackIssue(
                            kind=EvidenceIssueKind.PARSE_ERROR,
                            tool_name="inspect_calls",
                            count=parse_error_count,
                        )
                    )

                read_error_count = int(result.data.get("read_error_count", 0))
                if read_error_count > 0:
                    issues.append(
                        EvidencePackIssue(
                            kind=EvidenceIssueKind.TOOL_FAILURE,
                            tool_name="inspect_calls",
                            count=read_error_count,
                        )
                    )

                for match in result.data.get("matches", []):
                    candidates.append(
                        _EvidenceCandidate(
                            file_path=match["path"],
                            line_start=match["line_start"],
                            line_end=match["line_end"],
                            symbol=match["caller"],
                        )
                    )

        candidates = self._dedupe_candidates(candidates)
        if len(candidates) > request.limit:
            issues.append(
                EvidencePackIssue(
                    kind=EvidenceIssueKind.EVIDENCE_LIMIT,
                    count=len(candidates) - request.limit,
                )
            )
            candidates = candidates[: request.limit]

        evidence: list[CodeEvidence] = []
        read_results: dict[str, ToolResult | None] = {}
        provenance_integrity = True

        for candidate in candidates:
            if candidate.file_path not in read_results:
                read_results[candidate.file_path] = await self._invoke(
                    tool_name="read_file",
                    arguments={"path": candidate.file_path},
                    issues=issues,
                    trace_metadata=metadata,
                )

            read_result = read_results[candidate.file_path]
            if (
                read_result is None
                or not read_result.ok
                or read_result.data is None
            ):
                continue

            authoritative_path = read_result.data.get("path")
            content = read_result.data.get("content")
            if (
                authoritative_path != candidate.file_path
                or not isinstance(content, str)
            ):
                provenance_integrity = False
                issues.append(
                    EvidencePackIssue(
                        kind=EvidenceIssueKind.PROVENANCE_MISMATCH,
                        tool_name="read_file",
                        file_path=candidate.file_path,
                    )
                )
                continue

            lines = content.splitlines()
            if (
                candidate.line_start < 1
                or candidate.line_end < candidate.line_start
                or candidate.line_end > len(lines)
            ):
                provenance_integrity = False
                issues.append(
                    EvidencePackIssue(
                        kind=EvidenceIssueKind.PROVENANCE_MISMATCH,
                        tool_name="read_file",
                        file_path=candidate.file_path,
                    )
                )
                continue

            snippet = "\n".join(
                lines[candidate.line_start - 1 : candidate.line_end]
            )
            evidence.append(
                CodeEvidence(
                    repository=self._repository,
                    file_path=authoritative_path,
                    symbol=candidate.symbol,
                    line_start=candidate.line_start,
                    line_end=candidate.line_end,
                    snippet=snippet,
                )
            )

        pack = EvidencePack(
            query=request.query,
            task_intent=request.task_intent,
            evidence=evidence,
            provenance_integrity=provenance_integrity,
            incomplete=bool(issues),
            issues=issues,
        )
        self._emit_evidence_handoff(pack=pack, trace_metadata=metadata)
        return pack

    async def _invoke(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
        issues: list[EvidencePackIssue],
        trace_metadata: dict[str, Any],
    ) -> ToolResult | None:
        try:
            tool = self._registry.get(tool_name)
        except ToolNotFoundError:
            issues.append(
                EvidencePackIssue(
                    kind=EvidenceIssueKind.TOOL_UNAVAILABLE,
                    tool_name=tool_name,
                )
            )
            return None

        result = await self._runtime.invoke(
            tool=tool,
            arguments=arguments,
            trace_metadata=trace_metadata,
        )

        if not result.ok:
            issues.append(
                EvidencePackIssue(
                    kind=EvidenceIssueKind.TOOL_FAILURE,
                    tool_name=tool_name,
                    error_code=result.error_code,
                )
            )
            return result

        if result.truncated:
            issues.append(
                EvidencePackIssue(
                    kind=EvidenceIssueKind.TOOL_TRUNCATED,
                    tool_name=tool_name,
                )
            )

        return result

    def _emit_evidence_handoff(
        self,
        *,
        pack: EvidencePack,
        trace_metadata: dict[str, Any],
    ) -> None:
        if self._event_sink is None:
            return

        event = AgentEvent(
            trace_id=str(trace_metadata["trace_id"]),
            parent_event_id=self._metadata_parent_event_id(trace_metadata),
            event_type=AgentEventType.EVIDENCE_HANDOFF,
            component="repo_explorer",
            output_summary={
                "evidence_count": len(pack.evidence),
                "issue_count": len(pack.issues),
                "provenance_integrity": pack.provenance_integrity,
                "incomplete": pack.incomplete,
            },
            trace_metadata=dict(trace_metadata),
        )
        record_event_safely(self._event_sink, event)

    @staticmethod
    def _metadata_parent_event_id(
        trace_metadata: dict[str, Any],
    ) -> str | None:
        value = trace_metadata.get("parent_event_id")
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _dedupe_candidates(
        candidates: list[_EvidenceCandidate],
    ) -> list[_EvidenceCandidate]:
        seen: set[tuple[str, int, int, str | None]] = set()
        deduped: list[_EvidenceCandidate] = []

        for candidate in candidates:
            identity = (
                candidate.file_path,
                candidate.line_start,
                candidate.line_end,
                candidate.symbol,
            )
            if identity in seen:
                continue
            seen.add(identity)
            deduped.append(candidate)

        return deduped
