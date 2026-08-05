from app.answering.chunk_repository import ChunkRepository
from app.answering.context_builder import ContextBuilder
from app.answering.context_enricher import ContextEnricher
from app.answering.dto import Answer, BuiltContext, Citation, LLMAnswer
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
    ) -> None:
        self._retrieval_service = retrieval_service
        self._chunk_repository = chunk_repository
        self._context_enricher = context_enricher
        self._context_builder = context_builder
        self._evidence_verifier = evidence_verifier
        self._llm_provider = llm_provider
        self._workspace_repository = workspace_repository

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
                raise WorkspaceNotFoundError(
                    f"workspace {workspace_id} was not found"
                )

        hits = await self._retrieval_service.search(
            query=normalized_question,
            workspace_id=workspace_id,
            limit=retrieval_limit,
        )

        if not hits:
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
            raise AnsweringDataConsistencyError(
                "all retrieved chunks are missing from PostgreSQL"
            )

        built_context = self._context_builder.build(
            contexts=enriched.contexts,
        )

        if not built_context.sources:
            return self._build_refusal(question=normalized_question)

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
