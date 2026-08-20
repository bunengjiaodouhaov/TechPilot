from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.answering.deepseek_evidence_verifier import DeepSeekEvidenceVerifierProvider
from app.answering.evidence_dto import (
    EvidenceItem,
    EvidenceReason,
    EvidenceState,
    EvidenceVerificationInput,
)
from app.prompts.evidence_verifier import EVIDENCE_VERIFIER_PROMPT_VERSION


@dataclass(frozen=True)
class SmokeCase:
    name: str
    request: EvidenceVerificationInput
    expected_state: EvidenceState
    required_reasons: tuple[EvidenceReason, ...] = ()
    required_supporting_source_ids: tuple[str, ...] = ()
    required_conflicting_source_ids: tuple[str, ...] = ()


def build_cases() -> list[SmokeCase]:
    return [
        SmokeCase(
            name="sufficient",
            request=EvidenceVerificationInput(
                target="What is context engineering?",
                evidence=(
                    EvidenceItem(
                        source_id="SOURCE_1",
                        source_type="document",
                        source_ref="smoke-sufficient",
                        title="02_langchain_context_engineering.md",
                        locator="section=Overview > Why do agents fail?",
                        text=(
                            "Context engineering is providing the right information "
                            "and tools in the right format so the LLM can accomplish a task."
                        ),
                    ),
                ),
            ),
            expected_state=EvidenceState.SUFFICIENT,
            required_supporting_source_ids=("SOURCE_1",),
        ),
        SmokeCase(
            name="subject_mismatch",
            request=EvidenceVerificationInput(
                target="Which embedding model does TechPilot use?",
                evidence=(
                    EvidenceItem(
                        source_id="SOURCE_1",
                        source_type="document",
                        source_ref="smoke-subject-mismatch",
                        title="盘古大模型 PanguLargeModels 用户指南.pdf",
                        locator="page=770",
                        text=(
                            "ModelArts Studio supports deployment of the "
                            "Pangu-EmbeddingRank-zh model and provides an Embedding service."
                        ),
                    ),
                ),
            ),
            expected_state=EvidenceState.INSUFFICIENT,
            required_reasons=(EvidenceReason.SUBJECT_MISMATCH,),
        ),
        SmokeCase(
            name="conflicting",
            request=EvidenceVerificationInput(
                target="Which model is configured for retrieval?",
                evidence=(
                    EvidenceItem(
                        source_id="SOURCE_1",
                        source_type="document",
                        source_ref="smoke-conflict-a",
                        title="a.md",
                        text="The configured retrieval model is Model A.",
                    ),
                    EvidenceItem(
                        source_id="SOURCE_2",
                        source_type="document",
                        source_ref="smoke-conflict-b",
                        title="b.md",
                        text="The configured retrieval model is Model B.",
                    ),
                ),
            ),
            expected_state=EvidenceState.CONFLICTING,
            required_reasons=(EvidenceReason.CONFLICTING_EVIDENCE,),
            required_conflicting_source_ids=("SOURCE_1", "SOURCE_2"),
        ),
    ]


def assert_case(case: SmokeCase, *, result: object) -> None:
    actual = result
    if getattr(actual, "state") is not case.expected_state:
        raise AssertionError(
            f"{case.name}: expected state={case.expected_state.value}, "
            f"got {getattr(actual, 'state').value}"
        )

    actual_reasons = set(getattr(actual, "reasons"))
    missing_reasons = set(case.required_reasons) - actual_reasons
    if missing_reasons:
        raise AssertionError(
            f"{case.name}: missing required reasons: "
            + ", ".join(sorted(reason.value for reason in missing_reasons))
        )

    actual_supporting = set(getattr(actual, "supporting_source_ids"))
    missing_supporting = set(case.required_supporting_source_ids) - actual_supporting
    if missing_supporting:
        raise AssertionError(
            f"{case.name}: missing supporting sources: "
            + ", ".join(sorted(missing_supporting))
        )

    actual_conflicting = set(getattr(actual, "conflicting_source_ids"))
    missing_conflicting = set(case.required_conflicting_source_ids) - actual_conflicting
    if missing_conflicting:
        raise AssertionError(
            f"{case.name}: missing conflicting sources: "
            + ", ".join(sorted(missing_conflicting))
        )


async def main() -> None:
    from app.core.config import settings

    print("prompt_version:", EVIDENCE_VERIFIER_PROMPT_VERSION)
    provider = DeepSeekEvidenceVerifierProvider(
        api_key=settings.deepseek_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )

    cases = build_cases()
    for case in cases:
        result = await provider.verify(request=case.request)
        assert_case(case, result=result)
        print("=" * 80)
        print("case:", case.name)
        print("state:", result.state.value)
        print("reasons:", [reason.value for reason in result.reasons])
        print("supporting_source_ids:", list(result.supporting_source_ids))
        print("conflicting_source_ids:", list(result.conflicting_source_ids))
        print("explanation:", result.explanation)

    print()
    print(f"EVIDENCE VERIFIER SMOKE: PASS ({len(cases)}/{len(cases)})")


if __name__ == "__main__":
    asyncio.run(main())
