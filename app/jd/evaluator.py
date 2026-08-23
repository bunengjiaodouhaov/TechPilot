from __future__ import annotations

from dataclasses import dataclass

from .evaluation_schema import JDAnnotation
from .normalizer import SkillNormalizer
from .schemas import StructuredJD


@dataclass(frozen=True, slots=True)
class JDEvaluationResult:
    expected_count: int
    predicted_count: int
    matched_count: int
    requirement_recall: float
    requirement_precision: float
    hallucination_rate: float
    requirement_type_accuracy: float
    evidence_span_binding_rate: float


class JDEvaluator:
    def __init__(self, normalizer: SkillNormalizer | None = None) -> None:
        self._normalizer = normalizer or SkillNormalizer()

    def evaluate(
        self,
        *,
        jd_text: str,
        prediction: StructuredJD,
        golden: JDAnnotation,
    ) -> JDEvaluationResult:
        predicted = self._index_prediction(prediction)
        expected = self._index_golden(golden)

        matched_keys = set(predicted) & set(expected)
        expected_count = len(expected)
        predicted_count = len(predicted)
        matched_count = len(matched_keys)

        type_hits = sum(
            predicted[key].requirement_type == expected[key].requirement_type
            for key in matched_keys
        )

        bound_count = 0
        for requirement in prediction.requirements:
            span = requirement.evidence_span
            if (
                span.end <= len(jd_text)
                and jd_text[span.start : span.end] == span.text
                and requirement.raw_text == span.text
            ):
                bound_count += 1

        recall = matched_count / expected_count if expected_count else 1.0
        precision = matched_count / predicted_count if predicted_count else (
            1.0 if not expected_count else 0.0
        )
        hallucination = (
            (predicted_count - matched_count) / predicted_count
            if predicted_count
            else 0.0
        )

        return JDEvaluationResult(
            expected_count=expected_count,
            predicted_count=predicted_count,
            matched_count=matched_count,
            requirement_recall=recall,
            requirement_precision=precision,
            hallucination_rate=hallucination,
            requirement_type_accuracy=(
                type_hits / matched_count if matched_count else 0.0
            ),
            evidence_span_binding_rate=(
                bound_count / len(prediction.requirements)
                if prediction.requirements
                else 1.0
            ),
        )

    def _skill_key(self, value: str | None, fallback: str) -> str:
        raw = value or fallback
        return self._normalizer.normalize(raw).canonical_name.casefold()

    def _index_prediction(self, prediction: StructuredJD):
        return {
            self._skill_key(item.normalized_skill, item.raw_text): item
            for item in prediction.requirements
        }

    def _index_golden(self, golden: JDAnnotation):
        return {
            self._skill_key(item.skill, item.evidence_span.text): item
            for item in golden.requirements
        }
