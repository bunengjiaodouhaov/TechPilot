from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol

from pydantic import ValidationError

from .schemas import StructuredJD


class JDExtractionValidationError(ValueError):
    """Raised when structured JD output violates the domain contract."""


class JDExtractorProvider(Protocol):
    async def extract(self, jd_text: str) -> StructuredJD:
        ...


def bounded_structural_repair(payload: dict[str, Any]) -> dict[str, Any]:
    """Repair representation-only mistakes once.

    This deliberately does not invent requirements, enum meanings, missing
    evidence spans, or source offsets. Those require a model repair or a
    fail-closed result.
    """

    repaired = deepcopy(payload)
    requirements = repaired.get("requirements")
    if not isinstance(requirements, list):
        return repaired

    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        if isinstance(requirement.get("id"), int):
            requirement["id"] = str(requirement["id"])

    return repaired


def validate_structured_jd(
    *,
    jd_text: str,
    payload: dict[str, Any],
) -> StructuredJD:
    try:
        structured = StructuredJD.model_validate(payload)
    except ValidationError as exc:
        raise JDExtractionValidationError(str(exc)) from exc

    for requirement in structured.requirements:
        span = requirement.evidence_span
        if span.end > len(jd_text):
            raise JDExtractionValidationError(
                f"{requirement.id}: evidence span exceeds JD length"
            )

        bound_text = jd_text[span.start : span.end]
        if bound_text != span.text:
            raise JDExtractionValidationError(
                f"{requirement.id}: evidence span does not bind to original JD"
            )

        if requirement.raw_text != span.text:
            raise JDExtractionValidationError(
                f"{requirement.id}: raw_text must equal the exact evidence span"
            )

    return structured
