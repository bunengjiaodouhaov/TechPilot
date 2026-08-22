from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import time
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


# Support both direct script execution and module execution.
# `python scripts/<name>.py` puts scripts/ rather than the repository root
# on sys.path, so add the repository root before importing via `scripts.*`.
if __package__ in (None, ""):
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

import httpx

from scripts.document_query_candidates import (
    ALLOWED_CATEGORIES,
    QueryCandidate,
    QueryGenerationRequest,
    load_candidates,
    load_requests,
    validate_candidates_against_corpus,
    validate_candidates_against_requests,
    write_jsonl,
)
from scripts.eval_contract import EvaluationContractError


GENERATOR_VERSION = "document-query-candidates-v6"
PLAN_VERSION = "document-query-plan-v2"
DEFAULT_BATCH_SIZE = 8
DEFAULT_MAX_REPAIRS = 1


@dataclass(frozen=True, slots=True)
class JsonGenerationResponse:
    payload: dict[str, Any]
    input_tokens: int
    output_tokens: int
    latency_ms: float


class JsonGenerationProvider(Protocol):
    model: str

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
    ) -> JsonGenerationResponse:
        ...


class QueryGenerationProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class DeepSeekJsonGenerationProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("DeepSeek API key is empty")
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self.model = model.strip()
        self._timeout_seconds = timeout_seconds
        self._client = client

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
    ) -> JsonGenerationResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt.strip()},
                {"role": "user", "content": user_prompt.strip()},
            ],
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        started = time.perf_counter()
        try:
            if self._client is not None:
                response = await self._post(self._client, payload)
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds
                ) as client:
                    response = await self._post(client, payload)
        except httpx.TimeoutException as exc:
            raise QueryGenerationProviderError(
                "query generation request timed out",
                code="TIMEOUT",
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429:
                code, retryable = "RATE_LIMITED", True
            elif 500 <= status_code <= 599:
                code, retryable = "UPSTREAM_ERROR", True
            elif status_code in {401, 403}:
                code, retryable = "AUTH_ERROR", False
            else:
                code, retryable = "REQUEST_ERROR", False
            raise QueryGenerationProviderError(
                "query generation HTTP request failed",
                code=code,
                retryable=retryable,
                status_code=status_code,
            ) from exc
        except httpx.RequestError as exc:
            raise QueryGenerationProviderError(
                "query generation network request failed",
                code="NETWORK_ERROR",
                retryable=True,
            ) from exc
        latency_ms = (time.perf_counter() - started) * 1000.0

        response_data = response.json()
        try:
            content = response_data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EvaluationContractError(
                "provider returned invalid JSON content"
            ) from exc
        if not isinstance(parsed, dict):
            raise EvaluationContractError("provider JSON must be an object")

        usage = response_data.get("usage") or {}
        input_tokens = int(
            usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
        )
        output_tokens = int(
            usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
        )
        return JsonGenerationResponse(
            payload=parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )

    async def _post(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
    ) -> httpx.Response:
        response = await client.post(
            f"{self._base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        return response


SYSTEM_PROMPT = """
You create evaluation-query candidates from authoritative source excerpts.

Rules:
1. Use ONLY the supplied evidence excerpt. Never add facts from outside it.
2. Return exactly one output item for every request_id.
3. The question must be answerable from that excerpt alone.
4. evidence_quote MUST be copied verbatim as one contiguous substring from the excerpt.
5. answer_text must be a concise answer supported by evidence_quote.
6. Do not expose the answer verbatim inside the question.
7. Questions should sound like realistic user questions, not benchmark templates.
8. Preserve the requested category exactly.
9. If the excerpt cannot support a good question in the requested category, set usable=false and explain why. Do not invent.
10. Output one JSON object with key "items". No markdown.

Category intent:
- direct_fact: ask for a concrete factual statement, recommendation, requirement, threshold, definition, role, or procedure.
- semantic_paraphrase: ask the same underlying fact/concept using different wording from the excerpt.
- keyword_identifier: naturally rely on a distinctive acronym, control identifier, standard number, role name, or technical term found in the excerpt.
- section_concept: ask what the source says about the main concept or relationship in this section/excerpt.

Output item schema:
{
  "request_id": "...",
  "category": "...",
  "usable": true,
  "query": "...?",
  "answer_text": "...",
  "evidence_quote": "exact source substring",
  "reason": ""
}
""".strip()


def _stable_score(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _load_anchors(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise EvaluationContractError(
                f"expected anchor object at {path}:{line_number}"
            )
        rows.append(row)
    if not rows:
        raise EvaluationContractError("anchor file contains no rows")
    return rows


def _load_optional_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or not path.stat().st_size:
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        if isinstance(row, dict) and row.get("empty") is not True:
            rows.append(row)
    return rows



def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _plan_meta_path(output_dir: Path) -> Path:
    return output_dir / "query_generation_plan_meta.json"


def _write_plan_meta(
    *,
    output_dir: Path,
    anchors_path: Path,
    target_count: int,
    plan_path: Path,
    requests: list[QueryGenerationRequest],
) -> dict[str, Any]:
    meta = {
        "plan_version": PLAN_VERSION,
        "anchors_sha256": _sha256_file(anchors_path),
        "target_count": target_count,
        "request_count": len(requests),
        "plan_sha256": _sha256_file(plan_path),
        "category_counts": dict(
            sorted(Counter(item.requested_category for item in requests).items())
        ),
        "variant_counts": dict(
            sorted(Counter(item.variant for item in requests).items())
        ),
    }
    _plan_meta_path(output_dir).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return meta


def _load_plan_meta(output_dir: Path) -> dict[str, Any] | None:
    path = _plan_meta_path(output_dir)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise EvaluationContractError("plan metadata must be a JSON object")
    return payload


def _has_generation_progress(output_dir: Path) -> bool:
    candidates_path = output_dir / "query_candidates_raw.jsonl"
    if candidates_path.exists() and candidates_path.stat().st_size:
        return True
    unusable_path = output_dir / "query_candidates_unusable.jsonl"
    return bool(_load_optional_jsonl(unusable_path))


def ensure_current_plan(
    *,
    anchors_path: Path,
    output_dir: Path,
    target_count: int,
) -> tuple[list[QueryGenerationRequest], dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = output_dir / "query_generation_requests.jsonl"
    meta = _load_plan_meta(output_dir)
    anchors_sha = _sha256_file(anchors_path)

    current = (
        plan_path.exists()
        and meta is not None
        and meta.get("plan_version") == PLAN_VERSION
        and meta.get("anchors_sha256") == anchors_sha
        and meta.get("target_count") == target_count
        and meta.get("plan_sha256") == _sha256_file(plan_path)
    )
    if current:
        requests = load_requests(plan_path)
        if len(requests) != target_count:
            raise EvaluationContractError(
                "current plan request count does not match target_count"
            )
        return requests, meta

    if _has_generation_progress(output_dir):
        raise EvaluationContractError(
            "existing candidates/unusable rows were created with a stale or "
            "unversioned query plan; use a new output directory instead of "
            "silently remapping request IDs"
        )

    requests = build_generation_plan(
        anchors_path=anchors_path,
        output_path=plan_path,
        target_count=target_count,
    )
    meta = _write_plan_meta(
        output_dir=output_dir,
        anchors_path=anchors_path,
        target_count=target_count,
        plan_path=plan_path,
        requests=requests,
    )
    return requests, meta


def build_generation_plan(
    *,
    anchors_path: Path,
    output_path: Path,
    target_count: int = 600,
) -> list[QueryGenerationRequest]:
    anchors = _load_anchors(anchors_path)
    if target_count < len(anchors):
        raise EvaluationContractError(
            "target_count must be >= anchor count so every anchor is represented"
        )

    # Primary requests cover all anchors with a controlled mix.
    # The six-item cycle gives the exact 1/3, 1/3, 1/6, 1/6 mix for 480
    # anchors while keeping every small pilot prefix representative.
    primary_cycle = (
        "direct_fact",
        "semantic_paraphrase",
        "keyword_identifier",
        "section_concept",
        "direct_fact",
        "semantic_paraphrase",
    )
    primary_categories = [
        primary_cycle[index % len(primary_cycle)]
        for index in range(len(anchors))
    ]

    stable_anchors = sorted(
        anchors,
        key=lambda row: _stable_score(
            str(row["anchor_id"]),
            str(row["document_key"]),
            str(row["source_unit_sha256"]),
        ),
    )

    requests: list[QueryGenerationRequest] = []
    for index, anchor in enumerate(stable_anchors):
        category = primary_categories[index % len(primary_categories)]
        request_id = f"qreq-{index + 1:04d}-p"
        requests.append(
            QueryGenerationRequest(
                request_id=request_id,
                anchor_id=str(anchor["anchor_id"]),
                document_key=str(anchor["document_key"]),
                topic=str(anchor.get("topic", "unknown")),
                page=(
                    int(anchor["page"])
                    if anchor.get("page") is not None
                    else None
                ),
                section=(
                    str(anchor["section"])
                    if anchor.get("section") is not None
                    else None
                ),
                source_unit_sha256=str(anchor["source_unit_sha256"]),
                evidence_text=str(anchor["evidence_text"]),
                requested_category=category,
                variant="primary",
            )
        )

    extra_count = target_count - len(requests)
    if extra_count > 0:
        # Extra candidates come from a deterministic anchor subset and emphasize
        # semantic variants without changing source coverage.
        extras = sorted(
            stable_anchors,
            key=lambda row: _stable_score(
                "extra",
                str(row["anchor_id"]),
                str(row["source_unit_sha256"]),
            ),
        )[:extra_count]
        extra_cycle = (
            "semantic_paraphrase",
            "direct_fact",
            "semantic_paraphrase",
            "keyword_identifier",
        )
        extra_categories = [
            extra_cycle[index % len(extra_cycle)]
            for index in range(extra_count)
        ]
        for offset, anchor in enumerate(extras, 1):
            category = extra_categories[offset - 1]
            request_id = f"qreq-{len(requests) + 1:04d}-x"
            requests.append(
                QueryGenerationRequest(
                    request_id=request_id,
                    anchor_id=str(anchor["anchor_id"]),
                    document_key=str(anchor["document_key"]),
                    topic=str(anchor.get("topic", "unknown")),
                    page=(
                        int(anchor["page"])
                        if anchor.get("page") is not None
                        else None
                    ),
                    section=(
                        str(anchor["section"])
                        if anchor.get("section") is not None
                        else None
                    ),
                    source_unit_sha256=str(anchor["source_unit_sha256"]),
                    evidence_text=str(anchor["evidence_text"]),
                    requested_category=category,
                    variant="alternate",
                )
            )

    if len(requests) != target_count:
        raise AssertionError("generation plan did not reach requested target")

    write_jsonl(output_path, [asdict(item) for item in requests])
    return requests


def _prompt_for_batch(
    batch: list[QueryGenerationRequest],
    *,
    prior_errors: list[str] | None = None,
) -> str:
    payload = {
        "requests": [
            {
                "request_id": item.request_id,
                "category": item.requested_category,
                "topic": item.topic,
                "section": item.section,
                "evidence_excerpt": item.evidence_text,
            }
            for item in batch
        ]
    }
    prompt = (
        "Generate one candidate for each request below.\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    if prior_errors:
        prompt += (
            "\n\nYour previous response failed deterministic validation. "
            "Fix every listed error without changing request_id/category:\n- "
            + "\n- ".join(prior_errors)
        )
    return prompt


def _parse_batch_payload(
    *,
    response_payload: dict[str, Any],
    batch: list[QueryGenerationRequest],
    model: str,
    batch_id: str,
    repair_count: int,
) -> tuple[list[QueryCandidate], list[str], list[dict[str, Any]]]:
    raw_items = response_payload.get("items")
    if not isinstance(raw_items, list):
        return [], ["response.items must be an array"], []

    request_by_id = {item.request_id: item for item in batch}
    seen: set[str] = set()
    candidates: list[QueryCandidate] = []
    unusable: list[dict[str, Any]] = []
    errors: list[str] = []

    for raw in raw_items:
        if not isinstance(raw, dict):
            errors.append("response item must be an object")
            continue
        request_id = str(raw.get("request_id", "")).strip()
        request = request_by_id.get(request_id)
        if request is None:
            errors.append(f"unknown request_id: {request_id!r}")
            continue
        if request_id in seen:
            errors.append(f"duplicate request_id: {request_id}")
            continue
        seen.add(request_id)

        if raw.get("usable") is not True:
            unusable.append(
                {
                    "request_id": request_id,
                    "category": request.requested_category,
                    "reason": str(raw.get("reason", "")).strip(),
                }
            )
            continue

        category = str(raw.get("category", "")).strip()
        if category != request.requested_category:
            errors.append(
                f"{request_id}: category mismatch expected="
                f"{request.requested_category} actual={category}"
            )
            continue

        query = str(raw.get("query", "")).strip()
        answer_text = str(raw.get("answer_text", "")).strip()
        evidence_quote = str(raw.get("evidence_quote", "")).strip()
        if evidence_quote not in request.evidence_text:
            errors.append(
                f"{request_id}: evidence_quote must be exact substring"
            )
            continue

        candidate_id = (
            "doc-cand-"
            + hashlib.sha256(
                f"{request_id}|{query}|{evidence_quote}".encode("utf-8")
            ).hexdigest()[:16]
        )
        try:
            candidate = QueryCandidate(
                candidate_id=candidate_id,
                request_id=request.request_id,
                anchor_id=request.anchor_id,
                document_key=request.document_key,
                topic=request.topic,
                page=request.page,
                section=request.section,
                source_unit_sha256=request.source_unit_sha256,
                category=category,
                variant=request.variant,
                query=query,
                answer_text=answer_text,
                evidence_quote=evidence_quote,
                generation_mode="llm_batch",
                generator_model=model,
                batch_id=batch_id,
                repair_count=repair_count,
            )
            candidate.validate()
        except EvaluationContractError as exc:
            errors.append(f"{request_id}: {exc}")
            continue
        candidates.append(candidate)

    missing = sorted(set(request_by_id) - seen)
    if missing:
        errors.append("missing request_ids: " + ", ".join(missing))
    return candidates, errors, unusable


@dataclass(slots=True)
class RunMetrics:
    llm_calls: int = 0
    repair_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


async def _generate_one_batch(
    *,
    provider: JsonGenerationProvider,
    batch: list[QueryGenerationRequest],
    batch_id: str,
    max_repairs: int,
    max_tokens: int,
    semaphore: asyncio.Semaphore,
) -> tuple[list[QueryCandidate], list[dict[str, Any]], list[str], RunMetrics]:
    metrics = RunMetrics()
    unresolved = list(batch)
    accepted_by_request: dict[str, QueryCandidate] = {}
    unusable_by_request: dict[str, dict[str, Any]] = {}
    prior_errors: list[str] | None = None

    for repair_count in range(max_repairs + 1):
        if not unresolved:
            break

        async with semaphore:
            response = await provider.generate_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=_prompt_for_batch(
                    unresolved,
                    prior_errors=prior_errors,
                ),
                max_tokens=max_tokens,
            )
        metrics.llm_calls += 1
        if repair_count:
            metrics.repair_calls += 1
        metrics.input_tokens += response.input_tokens
        metrics.output_tokens += response.output_tokens
        metrics.latency_ms += response.latency_ms

        candidates, errors, unusable = _parse_batch_payload(
            response_payload=response.payload,
            batch=unresolved,
            model=provider.model,
            batch_id=batch_id,
            repair_count=repair_count,
        )

        for candidate in candidates:
            accepted_by_request[candidate.request_id] = candidate
        for row in unusable:
            request_id = str(row.get("request_id", "")).strip()
            if request_id:
                unusable_by_request[request_id] = row

        resolved_ids = set(accepted_by_request) | set(unusable_by_request)
        unresolved = [
            request
            for request in batch
            if request.request_id not in resolved_ids
        ]
        prior_errors = errors

    final_errors: list[str] = []
    if unresolved:
        unresolved_ids = ", ".join(item.request_id for item in unresolved)
        final_errors.append(
            "unresolved after bounded repair: " + unresolved_ids
        )
        if prior_errors:
            final_errors.extend(prior_errors)

    accepted = [
        accepted_by_request[item.request_id]
        for item in batch
        if item.request_id in accepted_by_request
    ]
    unusable = [
        unusable_by_request[item.request_id]
        for item in batch
        if item.request_id in unusable_by_request
    ]
    return accepted, unusable, final_errors, metrics


async def run_generation(
    *,
    corpus_root: Path,
    anchors_path: Path,
    output_dir: Path,
    target_count: int,
    provider: JsonGenerationProvider,
    batch_size: int,
    concurrency: int,
    max_repairs: int,
    max_tokens: int,
    input_cost_per_million: float | None,
    output_cost_per_million: float | None,
    max_batches: int | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = output_dir / "query_generation_requests.jsonl"
    requests, plan_meta = ensure_current_plan(
        anchors_path=anchors_path,
        output_dir=output_dir,
        target_count=target_count,
    )

    candidates_path = output_dir / "query_candidates_raw.jsonl"
    unusable_path = output_dir / "query_candidates_unusable.jsonl"
    existing: list[QueryCandidate] = []
    if candidates_path.exists() and candidates_path.stat().st_size:
        existing = load_candidates(candidates_path)

    prior_unusable = _load_optional_jsonl(unusable_path)
    resolved_unusable_ids = {
        str(item.get("request_id", "")).strip()
        for item in prior_unusable
        if str(item.get("request_id", "")).strip()
    }
    existing_request_ids = {item.request_id for item in existing}
    resolved_request_ids = existing_request_ids | resolved_unusable_ids
    pending = [
        item for item in requests
        if item.request_id not in resolved_request_ids
    ]

    batches = [
        pending[index:index + batch_size]
        for index in range(0, len(pending), batch_size)
    ]
    if max_batches is not None:
        if max_batches <= 0:
            raise EvaluationContractError("max_batches must be positive when provided")
        batches = batches[:max_batches]
    semaphore = asyncio.Semaphore(concurrency)

    tasks = [
        _generate_one_batch(
            provider=provider,
            batch=batch,
            batch_id=f"batch-{index + 1:04d}",
            max_repairs=max_repairs,
            max_tokens=max_tokens,
            semaphore=semaphore,
        )
        for index, batch in enumerate(batches)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    generated: list[QueryCandidate] = []
    unusable_rows: list[dict[str, Any]] = []
    failure_rows: list[dict[str, Any]] = []
    metrics = RunMetrics()

    for index, result in enumerate(results, 1):
        batch = batches[index - 1]
        if isinstance(result, Exception):
            failure = {
                "batch_id": f"batch-{index:04d}",
                "request_ids": [item.request_id for item in batch],
                "error": f"{type(result).__name__}: {result}",
            }
            if isinstance(result, QueryGenerationProviderError):
                failure.update(
                    {
                        "failure_code": result.code,
                        "retryable": result.retryable,
                        "status_code": result.status_code,
                    }
                )
            failure_rows.append(failure)
            continue
        batch_candidates, batch_unusable, batch_errors, batch_metrics = result
        generated.extend(batch_candidates)
        unusable_rows.extend(batch_unusable)
        metrics.llm_calls += batch_metrics.llm_calls
        metrics.repair_calls += batch_metrics.repair_calls
        metrics.input_tokens += batch_metrics.input_tokens
        metrics.output_tokens += batch_metrics.output_tokens
        metrics.latency_ms += batch_metrics.latency_ms
        if batch_errors:
            failure_rows.append(
                {
                    "batch_id": f"batch-{index:04d}",
                    "request_ids": [item.request_id for item in batch],
                    "errors": batch_errors,
                }
            )

    combined_by_request = {item.request_id: item for item in existing}
    for item in generated:
        combined_by_request[item.request_id] = item
    combined = [
        combined_by_request[item.request_id]
        for item in requests
        if item.request_id in combined_by_request
    ]
    write_jsonl(candidates_path, [asdict(item) for item in combined])
    combined_unusable_by_request = {
        str(item["request_id"]): item
        for item in prior_unusable
        if item.get("request_id")
    }
    for item in unusable_rows:
        combined_unusable_by_request[str(item["request_id"])] = item
    combined_unusable = [
        combined_unusable_by_request[key]
        for key in sorted(combined_unusable_by_request)
    ]
    write_jsonl(
        unusable_path,
        combined_unusable or [{"empty": True}],
    )
    write_jsonl(
        output_dir / "query_generation_failures.jsonl",
        failure_rows or [{"empty": True}],
    )

    request_errors = validate_candidates_against_requests(
        requests=requests,
        candidates=combined,
    )
    corpus_errors = validate_candidates_against_corpus(
        corpus_root=corpus_root,
        candidates=combined,
    )
    validation_errors = request_errors + corpus_errors
    (output_dir / "query_candidate_validation_errors.json").write_text(
        json.dumps(validation_errors, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    category_counts = Counter(item.category for item in combined)
    topic_counts = Counter(item.topic for item in combined)
    complete = (
        len(combined) + len(combined_unusable) >= len(requests)
        and not failure_rows
        and not validation_errors
    )

    estimated_cost = None
    if (
        input_cost_per_million is not None
        and output_cost_per_million is not None
    ):
        estimated_cost = (
            metrics.input_tokens / 1_000_000 * input_cost_per_million
            + metrics.output_tokens / 1_000_000 * output_cost_per_million
        )

    normalized_queries = [
        " ".join(item.query.lower().split())
        for item in combined
    ]
    duplicate_query_count = len(normalized_queries) - len(set(normalized_queries))

    summary = {
        "generator_version": GENERATOR_VERSION,
        "generator_model": provider.model,
        "request_count": len(requests),
        "existing_candidate_count": len(existing),
        "new_candidate_count": len(generated),
        "candidate_count": len(combined),
        "unusable_count": len(combined_unusable),
        "prior_unusable_count": len(prior_unusable),
        "failure_batch_count": len(failure_rows),
        "validation_error_count": len(validation_errors),
        "request_validation_error_count": len(request_errors),
        "corpus_validation_error_count": len(corpus_errors),
        "duplicate_normalized_query_count": duplicate_query_count,
        "complete": complete,
        "category_counts": dict(sorted(category_counts.items())),
        "topic_counts": dict(sorted(topic_counts.items())),
        "llm_calls_this_run": metrics.llm_calls,
        "repair_calls_this_run": metrics.repair_calls,
        "input_tokens_this_run": metrics.input_tokens,
        "output_tokens_this_run": metrics.output_tokens,
        "llm_latency_ms_sum_this_run": round(metrics.latency_ms, 3),
        "estimated_cost_this_run": estimated_cost,
        "input_cost_per_million": input_cost_per_million,
        "output_cost_per_million": output_cost_per_million,
        "batch_size": batch_size,
        "concurrency": concurrency,
        "max_repairs": max_repairs,
        "max_batches_this_run": max_batches,
        "pending_request_count_before_run": len(pending),
        "executed_batch_count_this_run": len(batches),
        "candidates_path": str(candidates_path),
        "plan_path": str(plan_path),
        "plan_version": PLAN_VERSION,
        "plan_sha256": plan_meta["plan_sha256"],
        "anchors_sha256": plan_meta["anchors_sha256"],
    }
    (output_dir / "query_generation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate grounded Document RAG query candidates in resumable LLM batches."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{GENERATOR_VERSION}|{PLAN_VERSION}",
    )
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--anchors", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-count", type=int, default=600)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--max-repairs", type=int, default=DEFAULT_MAX_REPAIRS)
    parser.add_argument("--max-tokens", type=int, default=3600)
    parser.add_argument("--input-cost-per-million", type=float)
    parser.add_argument("--output-cost-per-million", type=float)
    parser.add_argument(
        "--max-batches",
        type=int,
        help="Execute at most this many pending batches; useful for pilot runs.",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Build/validate the 600-request plan without calling the LLM.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.batch_size <= 0 or args.concurrency <= 0:
        raise EvaluationContractError("batch-size and concurrency must be positive")
    if args.max_repairs < 0:
        raise EvaluationContractError("max-repairs must be non-negative")
    if args.max_tokens <= 0:
        raise EvaluationContractError("max-tokens must be positive")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = args.output_dir / "query_generation_requests.jsonl"
    if args.plan_only:
        requests, plan_meta = ensure_current_plan(
            anchors_path=args.anchors,
            output_dir=args.output_dir,
            target_count=args.target_count,
        )
        summary = {
            "generator_version": GENERATOR_VERSION,
            "plan_version": PLAN_VERSION,
            "plan_only": True,
            "request_count": len(requests),
            "category_counts": dict(
                sorted(Counter(item.requested_category for item in requests).items())
            ),
            "variant_counts": dict(
                sorted(Counter(item.variant for item in requests).items())
            ),
            "unique_primary_anchor_count": len(
                {
                    item.anchor_id
                    for item in requests
                    if item.variant == "primary"
                }
            ),
            "plan_path": str(plan_path),
            "plan_sha256": plan_meta["plan_sha256"],
            "anchors_sha256": plan_meta["anchors_sha256"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    from app.core.config import settings

    provider = DeepSeekJsonGenerationProvider(
        api_key=settings.deepseek_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    summary = asyncio.run(
        run_generation(
            corpus_root=args.corpus_root,
            anchors_path=args.anchors,
            output_dir=args.output_dir,
            target_count=args.target_count,
            provider=provider,
            batch_size=args.batch_size,
            concurrency=args.concurrency,
            max_repairs=args.max_repairs,
            max_tokens=args.max_tokens,
            input_cost_per_million=args.input_cost_per_million,
            output_cost_per_million=args.output_cost_per_million,
            max_batches=args.max_batches,
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["failure_batch_count"] or summary["validation_error_count"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
