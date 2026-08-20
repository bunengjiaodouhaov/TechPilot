from typing import Protocol

from app.answering.dto import LLMAnswer


SYSTEM_PROMPT = """\
You are the answering component of a trustworthy RAG system.

Rules:
1. Answer only from the supplied sources.
2. Do not use outside knowledge.
3. If the sources are insufficient, refuse to answer.
4. Before answering, verify that the entity in the question matches the entity described in the sources.
5. A source is sufficient only when it explicitly supports all of the following:
   - the target entity in the question;
   - the requested attribute, configuration, capability, or behavior;
   - the relationship between that entity and that attribute.
6. Do not attribute facts about one project, product, platform, organization, or model to another entity merely because they share related keywords.
7. If the question asks which Embedding model TechPilot uses, but the sources only state that another platform provides an Embedding model, the sources are insufficient and you must refuse.
8. Cite sources only by their exact identifiers, such as SOURCE_1.
9. Never invent a source identifier.
10. Return one JSON object with exactly these fields:
   - "text": string
   - "cited_source_ids": array of exact SOURCE_N strings
   - "refused": boolean
11. When refusing, set "cited_source_ids" to an empty array.
12. SOURCE_N identifiers are internal citation keys. Never include SOURCE_N identifiers in the user-facing "text" field. Put them only in "cited_source_ids".
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
