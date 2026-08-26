from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import httpx


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.retrieval.reranker import CrossEncoderRerankerProvider
from scripts.docx_semantic_gap_eval import (
    DEFAULT_DATASET,
    _aggregate,
    _build_hybrid,
    _case_metrics,
    _rerank_candidates,
    _round_robin_union,
    load_cases,
)


DEFAULT_OUTPUT = Path(".local/p6/docx_semantic_gap_auto_query_eval.json")
DEFAULT_RERANKER = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class QueryDecompositionError(RuntimeError):
    pass


class DeepSeekQueryDecomposer:
    """Evaluation-only bounded retrieval query decomposer.

    This intentionally does not answer the user's question. It produces at
    most N retrieval-only subqueries while the original question remains the
    downstream answer target.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_subqueries: int,
        max_attempts: int = 3,
        client: httpx.AsyncClient,
    ) -> None:
        if not api_key.strip():
            raise ValueError("DEEPSEEK_API_KEY must be configured")
        if not base_url.strip():
            raise ValueError("base_url must not be empty")
        if not model.strip():
            raise ValueError("model must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_subqueries <= 0:
            raise ValueError("max_subqueries must be positive")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")

        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._model = model.strip()
        self._timeout_seconds = timeout_seconds
        self._max_subqueries = max_subqueries
        self._max_attempts = max_attempts
        self._client = client

    async def decompose(self, question: str) -> tuple[str, ...]:
        normalized = question.strip()
        if not normalized:
            raise ValueError("question must not be empty")

        system_prompt = (
            "You generate retrieval-only search queries for a document RAG system. "
            "Do not answer the question. Preserve named entities and constraints. "
            "If the question contains multiple technical aspects, split them into "
            "focused retrieval intents. Add useful domain terminology or synonyms "
            "only when they remain faithful to the original question. Avoid broad "
            "project-summary queries. Return JSON only as "
            '{"subqueries":["..."]}. '
            f"Return between 1 and {self._max_subqueries} subqueries. "
            "Each subquery must be concise, independently searchable, and no longer "
            "than 120 characters."
        )
        user_prompt = f"Original question:\n{normalized}"

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }

        last_error: BaseException | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self._timeout_seconds,
                )
                if response.status_code in _RETRYABLE_STATUS:
                    response.raise_for_status()
                response.raise_for_status()
                return self._parse(response)
            except (httpx.HTTPError, QueryDecompositionError) as exc:
                last_error = exc
                retryable = True
                if isinstance(exc, httpx.HTTPStatusError):
                    retryable = exc.response.status_code in _RETRYABLE_STATUS
                if not retryable or attempt >= self._max_attempts:
                    break
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))

        raise QueryDecompositionError(
            "automatic query decomposition failed"
        ) from last_error

    def _parse(self, response: httpx.Response) -> tuple[str, ...]:
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty model content")
            payload = json.loads(content)
            raw = payload["subqueries"]
        except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise QueryDecompositionError(
                "invalid query decomposition response"
            ) from exc

        if not isinstance(raw, list):
            raise QueryDecompositionError("subqueries must be an array")

        output: list[str] = []
        seen: set[str] = set()
        for value in raw:
            if not isinstance(value, str):
                continue
            text = value.strip()
            if not text or text == "":
                continue
            key = text.casefold()
            if key in seen:
                continue
            if len(text) > 120:
                text = text[:120].rstrip()
            seen.add(key)
            output.append(text)
            if len(output) >= self._max_subqueries:
                break

        if not output:
            raise QueryDecompositionError("no usable subqueries returned")
        return tuple(output)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


async def run_eval(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_cases(args.dataset)
    reranker = CrossEncoderRerankerProvider(
        model_name=args.reranker_model,
        batch_size=settings.reranker_batch_size,
        max_length=settings.reranker_max_length,
    )

    rows: list[dict[str, Any]] = []
    generation_latencies: list[float] = []
    retrieval_latencies: list[float] = []

    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        decomposer = DeepSeekQueryDecomposer(
            api_key=settings.deepseek_api_key,
            base_url=settings.llm_base_url,
            model=args.query_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_subqueries=args.max_subqueries,
            max_attempts=args.max_attempts,
            client=client,
        )

        async with AsyncSessionLocal() as session:
            hybrid = _build_hybrid(session)

            for index, case in enumerate(cases, start=1):
                print(f"[{index}/{len(cases)}] {case.case_id}")

                total_started = time.perf_counter()

                generation_started = time.perf_counter()
                subqueries = await decomposer.decompose(case.question)
                generation_ms = (
                    time.perf_counter() - generation_started
                ) * 1000.0
                generation_latencies.append(generation_ms)

                print("  subqueries:")
                for subquery in subqueries:
                    print(f"    - {subquery}")

                retrieval_started = time.perf_counter()
                rankings = []
                for query in (case.question, *subqueries):
                    hits = await hybrid.search(
                        query=query,
                        workspace_id=args.workspace_id,
                        candidate_limit=args.candidate_limit,
                        limit=args.per_query_limit,
                    )
                    rankings.append((query, hits))

                candidates, query_by_point = _round_robin_union(
                    rankings,
                    limit=args.rerank_depth,
                )
                ranked = await _rerank_candidates(
                    session=session,
                    provider=reranker,
                    candidates=candidates,
                    query_by_point=query_by_point,
                    fallback_query=case.question,
                    limit=args.final_limit,
                )
                retrieval_ms = (
                    time.perf_counter() - retrieval_started
                ) * 1000.0
                retrieval_latencies.append(retrieval_ms)

                row = _case_metrics(
                    case=case,
                    candidates=candidates,
                    ranked=ranked,
                    elapsed_ms=(time.perf_counter() - total_started) * 1000.0,
                )
                row["generated_subqueries"] = list(subqueries)
                row["query_generation_ms"] = generation_ms
                row["retrieval_rerank_ms"] = retrieval_ms
                rows.append(row)

    summary = _aggregate(rows)
    summary["query_generation_latency_ms"] = {
        "mean": statistics.fmean(generation_latencies),
        "p95": _p95(generation_latencies),
    }
    summary["retrieval_rerank_latency_ms"] = {
        "mean": statistics.fmean(retrieval_latencies),
        "p95": _p95(retrieval_latencies),
    }

    return {
        "config": {
            "dataset": str(args.dataset),
            "workspace_id": args.workspace_id,
            "query_model": args.query_model,
            "reranker_model": args.reranker_model,
            "max_subqueries": args.max_subqueries,
            "candidate_limit": args.candidate_limit,
            "rerank_depth": args.rerank_depth,
            "per_query_limit": args.per_query_limit,
            "final_limit": args.final_limit,
            "embedding_model": settings.embedding_model,
            "rrf_k": settings.answer_rrf_k,
        },
        "summary": summary,
        "cases": rows,
        "interpretation_guardrails": [
            "This is a curated DOCX semantic-gap regression set, not a public benchmark.",
            "Unlike the manual multi-query ceiling test, subqueries in this run are generated automatically by the configured LLM.",
            "Generated subqueries are retrieval-only; the original user question remains the answer target.",
            "Quality and latency must be compared with the manual ceiling and production baseline before enabling decomposition in production.",
            "A 12-case curated set is insufficient to claim general retrieval superiority across all corpora and languages.",
        ],
    }


def print_summary(report: dict[str, Any]) -> None:
    metrics = report["summary"]
    total = metrics["latency_ms"]
    generation = metrics["query_generation_latency_ms"]
    retrieval = metrics["retrieval_rerank_latency_ms"]

    print("\n=== AUTO QUERY SUMMARY ===")
    print(
        f"Recall@5={metrics['recall_at_5']:.4f} "
        f"Recall@20={metrics['recall_at_20']:.4f} "
        f"MRR@5={metrics['mrr_at_5']:.4f} "
        f"MRR@20={metrics['mrr_at_20']:.4f} "
        f"Any@5={metrics['any_hit_at_5']:.4f} "
        f"CandidateRecall={metrics['candidate_recall_at_depth']:.4f}"
    )
    print(
        f"query_generation mean={generation['mean']:.1f}ms "
        f"p95={generation['p95']:.1f}ms"
    )
    print(
        f"retrieval_rerank mean={retrieval['mean']:.1f}ms "
        f"p95={retrieval['p95']:.1f}ms"
    )
    print(
        f"end_to_end mean={total['mean']:.1f}ms "
        f"p95={total['p95']:.1f}ms"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate automatic bounded query decomposition on the curated "
            "EnterpriseOps DOCX semantic-gap regression set."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--workspace-id", type=int, required=True)
    parser.add_argument("--query-model", default=settings.llm_model)
    parser.add_argument("--reranker-model", default=DEFAULT_RERANKER)
    parser.add_argument("--max-subqueries", type=int, default=3)
    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=settings.answer_retrieval_candidate_limit,
    )
    parser.add_argument(
        "--rerank-depth",
        type=int,
        default=settings.answer_rerank_depth,
    )
    parser.add_argument("--per-query-limit", type=int, default=8)
    parser.add_argument("--final-limit", type=int, default=20)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    for field in (
        "workspace_id",
        "max_subqueries",
        "candidate_limit",
        "rerank_depth",
        "per_query_limit",
        "final_limit",
        "max_attempts",
    ):
        if getattr(args, field) <= 0:
            parser.error(f"--{field.replace('_', '-')} must be positive")

    if args.final_limit > args.rerank_depth:
        parser.error("--final-limit must not exceed --rerank-depth")
    if args.rerank_depth > args.candidate_limit * 2:
        parser.error(
            "--rerank-depth must not exceed twice --candidate-limit"
        )
    if args.per_query_limit > args.candidate_limit * 2:
        parser.error(
            "--per-query-limit must not exceed twice --candidate-limit"
        )

    return args


def main() -> None:
    args = parse_args()
    report = asyncio.run(run_eval(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print_summary(report)
    print(f"\nreport: {args.output}")


if __name__ == "__main__":
    main()
