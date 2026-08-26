from collections.abc import Awaitable, Callable

from app.answering.chunk_repository import ChunkRepository
from app.answering.context_builder import ContextBuilder
from app.answering.context_enricher import ContextEnricher
from app.answering.dto import (
    Answer,
    BuiltContext,
    Citation,
    LLMAnswer,
    RetrievedContext,
    StoredChunk,
)
from app.answering.evidence_dto import (
    EvidenceItem,
    EvidenceState,
    EvidenceVerificationInput,
    EvidenceVerificationResult,
)
from app.answering.evidence_verifier import (
    EvidenceVerifierProvider,
    validate_evidence_verification_result,
)
from app.answering.llm import SYSTEM_PROMPT, LLMProvider, build_user_prompt
from app.answering.workspace_repository import WorkspaceRepository
from app.retrieval.dense_retrieval_service import DenseRetrievalService
from app.retrieval.dto import VectorSearchHit


REFUSAL_TEXT = "现有文档中没有足够证据回答这个问题。"


class WorkspaceNotFoundError(LookupError):
    """Raised when the requested workspace does not exist."""


class AnsweringDataConsistencyError(RuntimeError):
    """Raised when vector hits cannot be resolved in PostgreSQL."""


class InvalidLLMCitationError(RuntimeError):
    """Raised when the LLM cites a source not verified for the answer."""


class UnexpectedLLMRefusalError(RuntimeError):
    """Raised when generation refuses after evidence was verified sufficient."""


class AnswerService:
    """Coordinate retrieval, evidence verification, generation and citations."""

    def __init__(
        self,
        *,
        retrieval_service: DenseRetrievalService,
        chunk_repository: ChunkRepository,
        context_enricher: ContextEnricher,
        context_builder: ContextBuilder,
        evidence_verifier: EvidenceVerifierProvider,
        llm_provider: LLMProvider,
        workspace_repository: WorkspaceRepository | None = None,
        release_read_transaction: Callable[[], Awaitable[None]] | None = None,
        recovery_enabled: bool = False,
        recovery_anchor_limit: int = 20,
        recovery_parent_group_limit: int = 2,
        recovery_max_additions: int = 12,
    ) -> None:
        if recovery_anchor_limit <= 0:
            raise ValueError("recovery_anchor_limit must be greater than zero")
        if recovery_parent_group_limit <= 0:
            raise ValueError("recovery_parent_group_limit must be greater than zero")
        if recovery_max_additions <= 0:
            raise ValueError("recovery_max_additions must be greater than zero")

        self._retrieval_service = retrieval_service
        self._chunk_repository = chunk_repository
        self._context_enricher = context_enricher
        self._context_builder = context_builder
        self._evidence_verifier = evidence_verifier
        self._llm_provider = llm_provider
        self._workspace_repository = workspace_repository
        self._release_read_transaction = release_read_transaction
        self._recovery_enabled = recovery_enabled
        self._recovery_anchor_limit = recovery_anchor_limit
        self._recovery_parent_group_limit = recovery_parent_group_limit
        self._recovery_max_additions = recovery_max_additions

    async def answer(
        self,
        *,
        question: str,
        workspace_id: int,
        retrieval_limit: int = 5,
    ) -> Answer:
        normalized_question = question.strip()

        if not normalized_question:
            raise ValueError("question must not be empty")
        if workspace_id <= 0:
            raise ValueError("workspace_id must be greater than zero")
        if retrieval_limit <= 0:
            raise ValueError("retrieval_limit must be greater than zero")

        if self._workspace_repository is not None:
            workspace_exists = await self._workspace_repository.exists(
                workspace_id=workspace_id,
            )
            if not workspace_exists:
                await self._release_reads()
                raise WorkspaceNotFoundError(
                    f"workspace {workspace_id} was not found"
                )

        hits = await self._retrieval_service.search(
            query=normalized_question,
            workspace_id=workspace_id,
            limit=retrieval_limit,
        )

        if not hits:
            await self._release_reads()
            return self._build_refusal(question=normalized_question)

        stored_chunks = await self._chunk_repository.get_by_ids(
            chunk_ids=[hit.point_id for hit in hits],
            workspace_id=workspace_id,
        )

        enriched = self._context_enricher.enrich(
            hits=hits,
            stored_chunks=stored_chunks,
        )

        if not enriched.contexts:
            await self._release_reads()
            raise AnsweringDataConsistencyError(
                "all retrieved chunks are missing from PostgreSQL"
            )

        built_context = self._context_builder.build(
            contexts=enriched.contexts,
        )

        if not built_context.sources:
            await self._release_reads()
            return self._build_refusal(question=normalized_question)

        # All PostgreSQL reads needed for the first verification are complete.
        # End the read transaction before verifier/generator network calls so
        # slow provider latency does not pin an otherwise idle DB transaction.
        await self._release_reads()

        verification_request = self._build_verification_input(
            question=normalized_question,
            built_context=built_context,
        )
        verification = await self._evidence_verifier.verify(
            request=verification_request,
        )
        validate_evidence_verification_result(
            request=verification_request,
            result=verification,
        )

        # Only insufficient evidence is eligible for one bounded recovery.
        # Conflicting evidence remains fail-closed and is never widened in an
        # attempt to make the conflict disappear.
        if (
            self._recovery_enabled
            and verification.state is EvidenceState.INSUFFICIENT
        ):
            recovered_context = await self._recover_structural_context(
                question=normalized_question,
                workspace_id=workspace_id,
                first_pass_contexts=enriched.contexts,
            )
            # Recovery performs fresh BM25/PostgreSQL reads after the first
            # verifier call, so release that second read transaction as well.
            await self._release_reads()

            if recovered_context is not None:
                built_context = recovered_context
                verification_request = self._build_verification_input(
                    question=normalized_question,
                    built_context=built_context,
                )
                verification = await self._evidence_verifier.verify(
                    request=verification_request,
                )
                validate_evidence_verification_result(
                    request=verification_request,
                    result=verification,
                )

        if verification.state is not EvidenceState.SUFFICIENT:
            return self._build_refusal(question=normalized_question)

        verified_source_ids = set(verification.supporting_source_ids)
        verified_sources = tuple(
            source
            for source in built_context.sources
            if source.source_id in verified_source_ids
        )
        verified_prompt_context = self._context_builder.render_sources(
            sources=verified_sources,
        )

        user_prompt = build_user_prompt(
            question=normalized_question,
            prompt_context=verified_prompt_context,
        )

        llm_answer = await self._llm_provider.generate(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        return self._build_answer(
            question=normalized_question,
            llm_answer=llm_answer,
            built_context=built_context,
            verification=verification,
        )

    async def _recover_structural_context(
        self,
        *,
        question: str,
        workspace_id: int,
        first_pass_contexts: tuple[RetrievedContext, ...],
    ) -> BuiltContext | None:
        """Use broad anchors to add a small amount of sibling evidence.

        Recovery prefers parent sections that were not already represented in
        the first-pass context. This makes the second chance complementary
        instead of spending its budget duplicating evidence regions already
        shown to the verifier.
        """

        anchors = await self._retrieval_service.search(
            query=question,
            workspace_id=workspace_id,
            limit=self._recovery_anchor_limit,
        )
        if not anchors:
            return None

        first_pass_parents = {
            (context.document_id, parent)
            for context in first_pass_contexts
            if (parent := self._parent_section(context.section)) is not None
        }
        groups = self._group_parent_sections(
            anchors,
            existing_parent_sections=first_pass_parents,
        )
        if not groups:
            return None

        selected_groups = groups[: self._recovery_parent_group_limit]
        sibling_chunks = await self._chunk_repository.get_by_parent_sections(
            parent_sections=[key for key, _ in selected_groups],
            workspace_id=workspace_id,
        )
        if not sibling_chunks:
            return None

        # The top-N anchors already had a chance to compete in the reranker.
        # Recovery budget is reserved for new sibling evidence, not duplicate
        # anchors or first-pass chunks.
        excluded_ids = {
            *(context.chunk_db_id for context in first_pass_contexts),
            *(hit.point_id for hit in anchors),
        }
        additions = self._rank_recovery_chunks(
            chunks=sibling_chunks,
            selected_groups=selected_groups,
            exclude_chunk_ids=excluded_ids,
        )[: self._recovery_max_additions]
        if not additions:
            return None

        next_rank = max(
            (context.rank for context in first_pass_contexts),
            default=0,
        ) + 1
        recovered_contexts: list[RetrievedContext] = list(first_pass_contexts)
        for offset, chunk in enumerate(additions):
            recovered_contexts.append(
                RetrievedContext(
                    chunk_db_id=chunk.chunk_db_id,
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    document_name=chunk.document_name,
                    source_type=chunk.source_type,
                    chunk_index=chunk.chunk_index,
                    section=chunk.section,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    text=chunk.text,
                    retrieval_score=0.0,
                    rank=next_rank + offset,
                )
            )

        recovered = self._context_builder.build(contexts=recovered_contexts)
        if len(recovered.sources) <= len(
            self._context_builder.build(contexts=first_pass_contexts).sources
        ):
            return None
        return recovered

    @staticmethod
    def _group_parent_sections(
        anchors: list[VectorSearchHit],
        *,
        existing_parent_sections: set[tuple[int, str]] | None = None,
    ) -> list[
        tuple[
            tuple[int, str],
            tuple[tuple[int, int], ...],
        ]
    ]:
        existing = existing_parent_sections or set()
        groups: dict[tuple[int, str], list[tuple[int, int]]] = {}
        for rank, hit in enumerate(anchors, start=1):
            parent = AnswerService._parent_section(hit.payload.section)
            if parent is None:
                continue
            key = (hit.payload.document_id, parent)
            groups.setdefault(key, []).append(
                (rank, hit.payload.chunk_index)
            )

        ordered = sorted(
            groups.items(),
            key=lambda item: (
                1 if item[0] in existing else 0,
                -len(item[1]),
                min(rank for rank, _ in item[1]),
                item[0][0],
                item[0][1],
            ),
        )
        return [
            (key, tuple(values))
            for key, values in ordered
        ]

    @staticmethod
    def _rank_recovery_chunks(
        *,
        chunks: list[StoredChunk],
        selected_groups: list[
            tuple[
                tuple[int, str],
                tuple[tuple[int, int], ...],
            ]
        ],
        exclude_chunk_ids: set[int],
    ) -> list[StoredChunk]:
        scored: list[tuple[tuple[int, int, int, int, int], StoredChunk]] = []

        for chunk in chunks:
            if chunk.chunk_db_id in exclude_chunk_ids:
                continue

            for group_order, ((document_id, parent), anchors) in enumerate(
                selected_groups
            ):
                if chunk.document_id != document_id:
                    continue
                if not AnswerService._belongs_to_parent(
                    section=chunk.section,
                    parent=parent,
                ):
                    continue

                support_count = len(anchors)
                best_anchor_rank = min(rank for rank, _ in anchors)
                distance = min(
                    abs(chunk.chunk_index - anchor_index)
                    for _, anchor_index in anchors
                )
                scored.append(
                    (
                        (
                            group_order,
                            -support_count,
                            best_anchor_rank,
                            distance,
                            chunk.chunk_index,
                        ),
                        chunk,
                    )
                )
                break

        scored.sort(key=lambda item: item[0])
        return [chunk for _, chunk in scored]

    @staticmethod
    def _parent_section(section: str | None) -> str | None:
        if section is None:
            return None
        normalized = section.strip()
        if not normalized or " > " not in normalized:
            return None
        parent = normalized.rsplit(" > ", 1)[0].strip()
        return parent or None

    @staticmethod
    def _belongs_to_parent(*, section: str | None, parent: str) -> bool:
        if section is None:
            return False
        normalized = section.strip()
        return normalized == parent or normalized.startswith(parent + " > ")

    async def _release_reads(self) -> None:
        if self._release_read_transaction is not None:
            await self._release_read_transaction()

    @staticmethod
    def _build_verification_input(
        *,
        question: str,
        built_context: BuiltContext,
    ) -> EvidenceVerificationInput:
        return EvidenceVerificationInput(
            target=question,
            evidence=tuple(
                EvidenceItem(
                    source_id=source.source_id,
                    text=source.included_text,
                    source_type=source.context.source_type,
                    source_ref=source.context.chunk_id,
                    title=source.context.document_name,
                    locator=AnswerService._build_evidence_locator(
                        section=source.context.section,
                        page_start=source.context.page_start,
                        page_end=source.context.page_end,
                    ),
                )
                for source in built_context.sources
            ),
        )

    @staticmethod
    def _build_evidence_locator(
        *,
        section: str | None,
        page_start: int | None,
        page_end: int | None,
    ) -> str | None:
        parts: list[str] = []
        if section:
            parts.append(f"section={section}")
        if page_start is not None or page_end is not None:
            start = page_start if page_start is not None else page_end
            end = page_end if page_end is not None else page_start
            if start == end:
                parts.append(f"page={start}")
            else:
                parts.append(f"pages={start}-{end}")
        return "; ".join(parts) or None

    @staticmethod
    def _build_refusal(*, question: str) -> Answer:
        return Answer(
            question=question,
            text=REFUSAL_TEXT,
            citations=(),
            refused=True,
        )

    @staticmethod
    def _build_answer(
        *,
        question: str,
        llm_answer: LLMAnswer,
        built_context: BuiltContext,
        verification: EvidenceVerificationResult,
    ) -> Answer:
        if llm_answer.refused:
            raise UnexpectedLLMRefusalError(
                "LLM refused after evidence verifier returned sufficient"
            )

        source_map = {source.source_id: source for source in built_context.sources}
        verified_source_ids = set(verification.supporting_source_ids)

        if not llm_answer.cited_source_ids:
            raise InvalidLLMCitationError(
                "LLM returned an answer without citing verified evidence"
            )

        invalid_source_ids = [
            source_id
            for source_id in llm_answer.cited_source_ids
            if source_id not in source_map or source_id not in verified_source_ids
        ]

        if invalid_source_ids:
            raise InvalidLLMCitationError(
                "LLM cited sources not verified as supporting: "
                + ", ".join(dict.fromkeys(invalid_source_ids))
            )

        citations = tuple(
            Citation(
                chunk_id=source_map[source_id].context.chunk_id,
                document_id=source_map[source_id].context.document_id,
                document_name=source_map[source_id].context.document_name,
                page_start=source_map[source_id].context.page_start,
                page_end=source_map[source_id].context.page_end,
                section=source_map[source_id].context.section,
                quote=source_map[source_id].included_text,
            )
            for source_id in dict.fromkeys(llm_answer.cited_source_ids)
        )

        return Answer(
            question=question,
            text=llm_answer.text,
            citations=citations,
            refused=False,
        )