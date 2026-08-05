from __future__ import annotations

from typing import Protocol

from app.answering.evidence_dto import (
    EvidenceReason,
    EvidenceState,
    EvidenceVerificationInput,
    EvidenceVerificationResult,
)



class EvidenceVerifierProvider(Protocol):
    """Provider-neutral interface for evidence sufficiency verification."""

    async def verify(
        self,
        *,
        request: EvidenceVerificationInput,
    ) -> EvidenceVerificationResult:
        """Verify whether supplied evidence supports the target."""
        ...


def build_evidence_verification_prompt(
    *,
    request: EvidenceVerificationInput,
) -> str:
    """Build a deterministic provider-neutral verifier prompt."""

    target = request.target.strip()
    if not target:
        raise ValueError("target must not be empty")

    evidence_blocks: list[str] = []
    seen_source_ids: set[str] = set()

    for item in request.evidence:
        source_id = item.source_id.strip()
        text = item.text.strip()

        if not source_id:
            raise ValueError("evidence source_id must not be empty")
        if source_id in seen_source_ids:
            raise ValueError(f"duplicate evidence source_id: {source_id}")
        if not text:
            raise ValueError(
                f"evidence text must not be empty for source_id={source_id}"
            )

        seen_source_ids.add(source_id)
        evidence_blocks.append(
            "\n".join(
                [
                    f"[{source_id}]",
                    f"source_type: {item.source_type}",
                    f"source_ref: {item.source_ref}",
                    f"title: {item.title or ''}",
                    f"locator: {item.locator or ''}",
                    "text:",
                    text,
                ]
            )
        )

    evidence_text = "\n\n".join(evidence_blocks) if evidence_blocks else "(none)"

    return "\n\n".join(
        [
            "Verification target:",
            target,
            "Evidence:",
            evidence_text,
            (
                "Return only the required JSON evidence-verification object. "
                "Do not answer the target itself."
            ),
        ]
    )


def validate_evidence_verification_result(
    *,
    request: EvidenceVerificationInput,
    result: EvidenceVerificationResult,
) -> None:
    """Validate provider-neutral evidence decision invariants."""

    if len(set(result.reasons)) != len(result.reasons):
        raise ValueError("evidence verification reasons contain duplicates")

    supporting = result.supporting_source_ids
    conflicting = result.conflicting_source_ids

    if len(set(supporting)) != len(supporting):
        raise ValueError("supporting_source_ids contain duplicates")
    if len(set(conflicting)) != len(conflicting):
        raise ValueError("conflicting_source_ids contain duplicates")

    allowed_source_ids = {item.source_id.strip() for item in request.evidence}
    referenced_source_ids = set(supporting) | set(conflicting)
    unknown_source_ids = sorted(referenced_source_ids - allowed_source_ids)
    if unknown_source_ids:
        raise ValueError(
            "evidence verification referenced unknown sources: "
            + ", ".join(unknown_source_ids)
        )

    overlap = sorted(set(supporting) & set(conflicting))
    if overlap:
        raise ValueError(
            "evidence verification source roles overlap: "
            + ", ".join(overlap)
        )

    if not request.evidence:
        if (
            result.state is not EvidenceState.INSUFFICIENT
            or result.reasons != (EvidenceReason.NO_EVIDENCE,)
            or supporting
            or conflicting
        ):
            raise ValueError(
                "empty evidence request must return insufficient/no_evidence"
            )
        return

    if EvidenceReason.NO_EVIDENCE in result.reasons:
        raise ValueError(
            "no_evidence reason is invalid when evidence was supplied"
        )

    if result.state is EvidenceState.SUFFICIENT:
        if result.reasons or conflicting or not supporting:
            raise ValueError(
                "sufficient evidence state is inconsistent with reasons or sources"
            )
        return

    if result.state is EvidenceState.INSUFFICIENT:
        if len(result.reasons) != 1 or conflicting:
            raise ValueError(
                "insufficient evidence state must contain exactly one primary reason"
            )
        if EvidenceReason.CONFLICTING_EVIDENCE in result.reasons:
            raise ValueError(
                "insufficient evidence state cannot use conflicting_evidence reason"
            )
        return

    if (
        EvidenceReason.CONFLICTING_EVIDENCE not in result.reasons
        or not conflicting
    ):
        raise ValueError(
            "conflicting evidence state is inconsistent with reasons or sources"
        )
