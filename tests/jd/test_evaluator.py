from app.jd.evaluation_schema import JDAnnotation
from app.jd.evaluator import JDEvaluator
from app.jd.schemas import StructuredJD


def test_evaluator_measures_recall_hallucination_type_and_binding():
    jd_text = "Python is required. RAG is preferred."

    prediction = StructuredJD.model_validate(
        {
            "requirements": [
                {
                    "id": "req-1",
                    "raw_text": "Python is required",
                    "normalized_skill": "Python",
                    "category": "technical",
                    "requirement_type": "required",
                    "evidence_span": {
                        "text": "Python is required",
                        "start": 0,
                        "end": 18,
                    },
                }
            ]
        }
    )

    golden = JDAnnotation.model_validate(
        {
            "id": "case-1",
            "requirements": [
                {
                    "skill": "Python",
                    "category": "technical",
                    "requirement_type": "required",
                    "evidence_span": {
                        "text": "Python is required",
                        "start": 0,
                        "end": 18,
                    },
                },
                {
                    "skill": "RAG",
                    "category": "technical",
                    "requirement_type": "preferred",
                    "evidence_span": {
                        "text": "RAG is preferred",
                        "start": 20,
                        "end": 36,
                    },
                },
            ],
        }
    )

    result = JDEvaluator().evaluate(
        jd_text=jd_text,
        prediction=prediction,
        golden=golden,
    )

    assert result.requirement_recall == 0.5
    assert result.requirement_precision == 1.0
    assert result.hallucination_rate == 0.0
    assert result.requirement_type_accuracy == 1.0
    assert result.evidence_span_binding_rate == 1.0
