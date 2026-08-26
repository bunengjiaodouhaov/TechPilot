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

from app.answering.chunk_repository import ChunkRepository
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.retrieval.hybrid_dto import HybridSearchHit
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


DEFAULT_OUTPUT = Path(".local/p6/docx_semantic_gap_grounded_query_eval.json")
DEFAULT_RERANKER = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class GroundedQueryDecompositionError(RuntimeError):
    pass


class DeepSeekGroundedQueryDecomposer:
    """Evaluation-only PRF-grounded retrieval query decomposer."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_subqueries: int,
        max_attempts: int,
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

    async def decompose(
        self,
        *,
        question: str,
        grounding_context: str,
    ) -> tuple[str, ...]:
        normalized_question = question.strip()
        normalized_grounding = grounding_context.strip()
        if not normalized_question:
            raise ValueError("question must not be empty")
        if not normalized_grounding:
            raise ValueError("grounding_context must not be empty")

        system_prompt = (
            "You generate retrieval-only search queries for a document RAG system. "
            "Do not answer the user's question. The supplied first-pass candidates "
            "are pseudo relevance feedback: use their project-specific terminology "
            "as vocabulary anchors, but do not assume every candidate is correct. "
            "Generate focused queries that can recover missing implementation "
            "details. Preserve named entities and the user's intent. Prefer concrete "
            "mechanisms, boundaries, failure modes, validation, policy classes, "
            "state transitions, or protocols when the candidate text hints at them. "
            "Do not merely paraphrase the original question. Do not invent product "
            "names or mechanisms absent from both the question and candidate text. "
            "Return JSON only as {\"subqueries\":[\"...\"]}. "
            f"Return between 1 and {self._max_subqueries} subqueries. "
            "Each subquery must be concise, independently searchable, and no longer "
            "than 140 characters."
        )
        user_prompt = (
            f"Original question:\n{normalized_question}\n\n"
            "First-pass candidate context:\n"
            f"{normalized_grounding}"
        )
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
                response.raise_for_status()
                return self._parse(response)
            except (httpx.HTTPError, GroundedQueryDecompositionError) as exc:
                last_error = exc
                retryable = True
                if isinstance(exc, httpx.HTTPStatusError):
                    retryable = exc.response.status_code in _RETRYABLE_STATUS
                if not retryable or attempt >= self._max_attempts:
                    break
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))

        raise GroundedQueryDecompositionError(
            "grounded query decomposition failed"
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
            raise GroundedQueryDecompositionError(
                "invalid grounded query decomposition response"
            ) from exc

        if not isinstance(raw, list):
            raise GroundedQueryDecompositionError("subqueries must be an array")

        output: list[str] = []
        seen: set[str] = set()
        for value in raw:
            if not isinstance(value, str):
                continue
            text = value.strip()
            if not text:
                continue
            if len(text) > 140:
                text = text[:140].rstrip()
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            output.append(text)
            if len(output) >= self._max_subqueries:
                break

        if not output:
            raise GroundedQueryDecompositionError("no usable subqueries returned")
        return tuple(output)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


async def _build_grounding_context(
    *,
    session,
    hits: list[HybridSearchHit],
    snippet_chars: int,
) -> str:
    if not hits:
        return "No first-pass candidates."

    repo = ChunkRepository(session=session)
    chunks = await repo.get_by_ids(
        chunk_ids=[hit.point_id for hit in hits],
        workspace_id=hits[0].workspace_id,
    )

    lines: list[str] = []
    for rank, hit in enumerate(hits, start=1):
        stored = chunks.get(hit.point_id)
        if stored is None:
            continue
        section = hit.section or "(no section)"
        snippet = " ".join(stored.text.split())[:snippet_chars]
        lines.append(
            f"[{rank}] section={section}\n"
            f"snippet={snippet}"
        )

    await session.commit()
    return "\n\n".join(lines) or "No usable first-pass candidates."


async def run_eval(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_cases(args.dataset)
    reranker = CrossEncoderRerankerProvider(
        model_name=args.reranker_model,
        batch_size=settings.reranker_batch_size,
        max_length=settings.reranker_max_length,
    )

    rows: list[dict[str, Any]] = []
    first_pass_latencies: list[float] = []
    generation_latencies: list[float] = []
    recovery_latencies: list[float] = []

    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        decomposer = DeepSeekGroundedQueryDecomposer(
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

                first_started = time.perf_counter()
                first_hits = await hybrid.search(
                    query=case.question,
                    workspace_id=args.workspace_id,
                    candidate_limit=args.candidate_limit,
                    limit=args.grounding_top_k,
                )
                grounding_context = await _build_grounding_context(
                    session=session,
                    hits=list(first_hits),
                    snippet_chars=args.snippet_chars,
                )
                first_ms = (time.perf_counter() - first_started) * 1000.0
                first_pass_latencies.append(first_ms)

                generation_started = time.perf_counter()
                subqueries = await decomposer.decompose(
                    question=case.question,
                    grounding_context=grounding_context,
                )
                generation_ms = (
                    time.perf_counter() - generation_started
                ) * 1000.0
                generation_latencies.append(generation_ms)

                print("  grounded subqueries:")
                for subquery in subqueries:
                    print(f"    - {subquery}")

                recovery_started = time.perf_counter()
                rankings: list[tuple[str, list[HybridSearchHit]]] = []
                for query in (case.question, *subqueries):
                    hits = await hybrid.search(
                        query=query,
                        workspace_id=args.workspace_id,
                        candidate_limit=args.candidate_limit,
                        limit=args.per_query_limit,
                    )
                    rankings.append((query, list(hits)))

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
                recovery_ms = (
                    time.perf_counter() - recovery_started
                ) * 1000.0
                recovery_latencies.append(recovery_ms)

                row = _case_metrics(
                    case=case,
                    candidates=candidates,
                    ranked=ranked,
                    elapsed_ms=(time.perf_counter() - total_started) * 1000.0,
                )
                row["generated_subqueries"] = list(subqueries)
                row["first_pass_point_ids"] = [hit.point_id for hit in first_hits]
                row["first_pass_sections"] = [hit.section for hit in first_hits]
                row["first_pass_ms"] = first_ms
                row["query_generation_ms"] = generation_ms
                row["recovery_retrieval_rerank_ms"] = recovery_ms
                rows.append(row)

    summary = _aggregate(rows)
    summary["first_pass_latency_ms"] = {
        "mean": statistics.fmean(first_pass_latencies),
        "p95": _p95(first_pass_latencies),
    }
    summary["query_generation_latency_ms"] = {
        "mean": statistics.fmean(generation_latencies),
        "p95": _p95(generation_latencies),
    }
    summary["recovery_latency_ms"] = {
        "mean": statistics.fmean(recovery_latencies),
        "p95": _p95(recovery_latencies),
    }

    return {
        "config": {
            "dataset": str(args.dataset),
            "workspace_id": args.workspace_id,
            "query_model": args.query_model,
            "reranker_model": args.reranker_model,
            "max_subqueries": args.max_subqueries,
            "grounding_top_k": args.grounding_top_k,
            "snippet_chars": args.snippet_chars,
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
            "This is an evaluation-only grounded recovery probe, not the production answer path.",
            "Grounding uses only first-pass retrieved candidates; no gold labels or manual subqueries are exposed to the decomposer.",
            "First-pass candidate text is pseudo relevance feedback and may contain false positives.",
            "Generated subqueries remain retrieval-only; the original user question remains the answer target.",
            "The curated 12-case set is a regression probe, not a general benchmark.",
        ],
    }


def print_summary(report: dict[str, Any]) -> None:
    metrics = report["summary"]
    total = metrics["latency_ms"]
    first_pass = metrics["first_pass_latency_ms"]
    generation = metrics["query_generation_latency_ms"]
    recovery = metrics["recovery_latency_ms"]

    print("\n=== GROUNDED QUERY SUMMARY ===")
    print(
        f"Recall@5={metrics['recall_at_5']:.4f} "
        f"Recall@20={metrics['recall_at_20']:.4f} "
        f"MRR@5={metrics['mrr_at_5']:.4f} "
        f"MRR@20={metrics['mrr_at_20']:.4f} "
        f"Any@5={metrics['any_hit_at_5']:.4f} "
        f"CandidateRecall={metrics['candidate_recall_at_depth']:.4f}"
    )
    print(
        f"first_pass mean={first_pass['mean']:.1f}ms "
        f"p95={first_pass['p95']:.1f}ms"
    )
    print(
        f"query_generation mean={generation['mean']:.1f}ms "
        f"p95={generation['p95']:.1f}ms"
    )
    print(
        f"recovery mean={recovery['mean']:.1f}ms "
        f"p95={recovery['p95']:.1f}ms"
    )
    print(
        f"end_to_end mean={total['mean']:.1f}ms "
        f"p95={total['p95']:.1f}ms"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate first-pass-grounded query recovery on the curated "
            "EnterpriseOps DOCX semantic-gap regression set."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--workspace-id", type=int, required=True)
    parser.add_argument("--query-model", default=settings.llm_model)
    parser.add_argument("--reranker-model", default=DEFAULT_RERANKER)
    parser.add_argument("--max-subqueries", type=int, default=3)
    parser.add_argument("--grounding-top-k", type=int, default=12)
    parser.add_argument("--snippet-chars", type=int, default=320)
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
        "grounding_top_k",
        "snippet_chars",
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
    if args.grounding_top_k > args.candidate_limit * 2:
        parser.error(
            "--grounding-top-k must not exceed twice --candidate-limit"
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
