from typing import Protocol

from app.answering.dto import LLMAnswer


SYSTEM_PROMPT = """\
You are the answering component of a trustworthy RAG system.

Rules:
1. Answer only from the supplied sources.
2. Do not use outside knowledge.
3. If the sources are insufficient, refuse to answer.
4. Cite sources only by their exact identifiers, such as SOURCE_1.
5. Never invent a source identifier.
6. Return one JSON object with exactly these fields:
   - "text": string
   - "cited_source_ids": array of exact SOURCE_N strings
   - "refused": boolean
7. When refusing, set "cited_source_ids" to an empty array.
"""


class LLMProvider(Protocol):
    """Provider-neutral interface for text generation."""

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMAnswer:
        """Generate and parse one provider-neutral LLM answer."""
        ...


def build_user_prompt(
    *,
    question: str,
    prompt_context: str,
) -> str:
    """Build the provider-neutral user prompt."""

    normalized_question = question.strip()
    normalized_context = prompt_context.strip()

    if not normalized_question:
        raise ValueError("question must not be empty")

    if not normalized_context:
        raise ValueError("prompt_context must not be empty")

    return "\n\n".join(
        [
            "Question:",
            normalized_question,
            "Sources:",
            normalized_context,
            (
                "Return an answer based only on the sources. "
                "Also identify the exact SOURCE_N identifiers used. "
                "If the sources are insufficient, refuse to answer."
            ),
        ]
    )
