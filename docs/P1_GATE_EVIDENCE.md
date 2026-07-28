# TechPilot P1 Gate Evidence

## Purpose

This document assembles reproducible P1 evidence for the Day 10 Gate Review.
It does not declare `PASS`, `CONDITIONAL PASS`, or `FIX`.

## Review scope

P1 covers the first trustworthy document-RAG loop:

- Markdown and text-based PDF ingestion
- structure-aware chunking and source metadata
- multilingual E5 dense retrieval through Qdrant
- retrieval evaluation with a manually labelled dataset
- Context construction and LLM answering
- server-owned Citation binding
- evidence-insufficient refusal
- soft deletion and deleted-document isolation

README and milestone completion remain deferred until the Day 10 Gate decision.

## Evidence matrix

| Requirement | Evidence | Result | Interpretation |
|---|---|---|---|
| Markdown and text-based PDF ingestion | Real upload E2E; parser/chunker tests; active local corpus includes Markdown and a 1002-chunk text PDF | PASS | OCR is outside P1 scope |
| Source structure preservation | Day 8 Markdown heading-path and PDF page-range propagation tests | PASS | Native source location reaches server Citation |
| Dense retrieval | `intfloat/multilingual-e5-base`, 768-dimensional normalized vectors, Qdrant workspace filter | PASS | PostgreSQL remains the text source of truth |
| Retrieval baseline | 30 manually labelled cases; Recall@5 `0.866667`; MRR@5 `0.627778`; 4 misses retained locally | PASS | Baseline is measurable and reproducible; misses are not hidden |
| Trusted answer orchestration | Real DeepSeek Answer E2E plus deterministic lifecycle integration test | PASS | Real-provider compatibility and deterministic orchestration are covered separately |
| Citation traceability | LLM returns `SOURCE_N`; server binds document, page/section and quote from actual Context sources | PASS | The model cannot freely invent Citation metadata |
| Context-budget isolation | Omitted-source Citation rejection regression | PASS | A Chunk that did not enter the prompt cannot become a valid citation |
| Document deletion | PostgreSQL soft delete followed by best-effort Qdrant cleanup; retrieval and Chunk lookup exclude deleted documents | PASS | PostgreSQL state prevents deleted evidence from being used even if vector cleanup is delayed |
| Full P1 lifecycle | Upload -> persist -> index -> retrieve -> answer -> cite -> delete -> refuse | PASS | Integration test uses real PostgreSQL, Embedding and Qdrant with a prompt-checking deterministic Fake LLM |
| Unanswerable safety | 10 corpus-grounded difficult negative cases; 10 correct refusals; 0 incorrect answers; 0 runtime errors | PASS | Incorrect-answer rate is `0.000000` |
| Full regression suite | `pytest -q`: 126 passed | PASS | Starlette/httpx TestClient deprecation warning is non-blocking |
| Repository hygiene | `git diff --check`: clean | PASS | No whitespace errors detected |
| README and milestone declaration | Intentionally deferred | PENDING DAY 10 | Must follow, not precede, the Gate decision |

## Day 9 lifecycle integration evidence

Test:

```bash
pytest -q tests/integration/test_p1_document_rag_lifecycle.py
```

The test verifies:

1. A temporary Workspace is created.
2. Markdown is uploaded through the HTTP API.
3. Chunks are persisted in PostgreSQL and indexed in Qdrant.
4. Dense retrieval returns the uploaded evidence.
5. The actual prompt contains `[SOURCE_1]` and the unique uploaded marker.
6. The answer is not refused and the server Citation contains the exact document name, heading path and quote.
7. The document is soft-deleted through the HTTP API.
8. Re-answering returns `refused=true` with no Citation and does not call the Fake LLM again.
9. Temporary PostgreSQL and Qdrant data are cleaned.

The Fake LLM is intentional. It validates deterministic orchestration and the model boundary without turning the integration test into a network- and provider-dependent quality test. Real DeepSeek compatibility is covered by the Day 6 E2E.

## Trusted-answering evaluation evidence

Command:

```bash
PYTHONPATH=. python scripts/answer_eval.py \
  --dataset eval/answer_golden.jsonl \
  --output eval/answer_results.jsonl \
  --retrieval-limit 5
```

Dataset boundary:

- Workspace: `2`
- Active documents: `5`
- Active Chunks: `1153`
- Cases: `10`
- All cases are `answerable=false`
- Cases were generated only after inspecting the active corpus, including the 1002-chunk Pangu PDF

Observed summary:

```text
cases: 10
runtime_errors: 0
answerable_cases: 0
evaluated_answerable_cases: 0
over_refusals: 0
over_refusal_rate: n/a
unanswerable_cases: 10
evaluated_unanswerable_cases: 10
correct_refusals: 10
incorrect_answers: 0
incorrect_answer_rate: 0.000000
```

Interpretation boundary:

- This dataset proves unanswerable safety, not answerable quality.
- `over_refusal_rate` is `n/a`, not `0%`, because there are no answerable cases in this dataset.
- Answerable behavior is evidenced separately by the real Answer E2E, lifecycle integration test and Citation regressions.
- A future quantitative answer-quality dataset should contain both normal and confusing `answerable=true` cases with reference answers and expected source documents.

## Stability issue found during integration

The initial full-suite run intermittently produced `503` in `test_dependencies_health` after the async lifecycle test, while a standalone dependency request returned `200`.

Root cause: the global SQLAlchemy AsyncEngine could retain pooled asyncpg connections bound to the lifecycle test event loop. A later synchronous TestClient test runs the application on another event loop.

Resolution: dispose the global engine in the lifecycle test cleanup so later tests establish fresh connections. Production request behavior was not changed.

Validation after the fix:

- lifecycle test followed by dependency health test: PASS
- full suite: 126 passed
- dependency health test repeated three times: PASS
- `git diff --check`: PASS

## Known non-blocking limitations

- Only Markdown and text-based PDF are supported; scanned PDF OCR is not implemented.
- Retrieval is dense Top-K only; Hybrid Retrieval and Reranker are outside P1.
- Context budget uses character count rather than tokenizer tokens.
- Qdrant deletion is best-effort; the long-term reliability pattern is an Outbox.
- Starlette/httpx TestClient emits a deprecation warning.
- The local `eval/` datasets and raw result files are intentionally not committed.

## Day 10 review questions

Day 10 must decide:

1. Do the technical exit conditions support `PASS`, `CONDITIONAL PASS`, or `FIX`?
2. Are the listed limitations non-blocking for P1?
3. Is the absence of a quantitative answerable-quality score acceptable given the existing real E2E and deterministic Citation evidence?
4. After the decision, which README, milestone and project-status declarations must be updated?
