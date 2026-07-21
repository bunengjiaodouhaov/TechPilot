from collections.abc import Sequence

from app.answering.dto import (
    BuiltContext,
    BuiltContextSource,
    RetrievedContext,
)


class ContextBuilder:
    """Build prompt-ready evidence context from retrieved chunks."""

    def __init__(self, *, max_characters: int) -> None:
        if max_characters <= 0:
            raise ValueError("max_characters must be greater than zero")

        self._max_characters = max_characters

    def build(
        self,
        *,
        contexts: Sequence[RetrievedContext],
    ) -> BuiltContext:
        """Build context while preserving complete evidence chunks."""

        ordered_contexts = sorted(
            contexts,
            key=lambda context: context.rank,
        )

        unique_contexts = self._deduplicate(ordered_contexts)

        included_sources: list[BuiltContextSource] = []
        prompt_blocks: list[str] = []

        for context in unique_contexts:
            source_id = f"SOURCE_{len(included_sources) + 1}"

            block = self._format_source_block(
                source_id=source_id,
                context=context,
            )

            candidate_prompt = "\n\n".join(
                [*prompt_blocks, block]
            )

            if len(candidate_prompt) > self._max_characters:
                break

            prompt_blocks.append(block)

            included_sources.append(
                BuiltContextSource(
                    source_id=source_id,
                    context=context,
                    included_text=context.text,
                )
            )

        prompt_context = "\n\n".join(prompt_blocks)

        return BuiltContext(
            prompt_context=prompt_context,
            sources=tuple(included_sources),
            omitted_count=(
                len(unique_contexts) - len(included_sources)
            ),
            character_count=len(prompt_context),
        )

    @staticmethod
    def _deduplicate(
        contexts: Sequence[RetrievedContext],
    ) -> list[RetrievedContext]:
        """Keep the first ranked occurrence of each database chunk."""

        unique_contexts: list[RetrievedContext] = []
        seen_chunk_ids: set[int] = set()

        for context in contexts:
            if context.chunk_db_id in seen_chunk_ids:
                continue

            seen_chunk_ids.add(context.chunk_db_id)
            unique_contexts.append(context)

        return unique_contexts

    @staticmethod
    def _format_source_block(
        *,
        source_id: str,
        context: RetrievedContext,
    ) -> str:
        """Format one authoritative source block for the LLM."""

        page = ContextBuilder._format_page_range(
            page_start=context.page_start,
            page_end=context.page_end,
        )

        section = context.section or "N/A"

        return "\n".join(
            [
                f"[{source_id}]",
                f"document: {context.document_name}",
                f"page: {page}",
                f"section: {section}",
                f"chunk_id: {context.chunk_id}",
                "content:",
                context.text,
            ]
        )

    @staticmethod
    def _format_page_range(
        *,
        page_start: int | None,
        page_end: int | None,
    ) -> str:
        if page_start is None and page_end is None:
            return "N/A"

        if page_start is None:
            return str(page_end)

        if page_end is None or page_end == page_start:
            return str(page_start)

        return f"{page_start}-{page_end}"
