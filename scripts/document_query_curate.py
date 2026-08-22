from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


if __package__ in (None, ""):
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

from scripts.document_query_candidates import QueryCandidate, load_candidates
from scripts.eval_contract import EvaluationContractError, sha256_file


CURATOR_VERSION = "document-query-curator-v1"

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*")
_WS = re.compile(r"\s+")
_BENCHMARK_PHRASES = (
    "according to the excerpt",
    "according to this excerpt",
    "in the excerpt",
    "provided excerpt",
    "source excerpt",
    "the passage",
    "provided passage",
)
_GENERIC_QUESTIONS = (
    re.compile(r"^what (?:does|is) (?:the )?(?:excerpt|passage|text) (?:say|describe)\??$", re.I),
    re.compile(r"^what information is provided\??$", re.I),
    re.compile(r"^what is described\??$", re.I),
)


@dataclass(frozen=True, slots=True)
class CuratedCandidate:
    candidate: QueryCandidate
    quality_score: int
    flags: tuple[str, ...]
    review_priority: str
    exact_duplicate_of: str | None = None
    same_anchor_near_duplicate_of: str | None = None
    cross_document_near_duplicate_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self.candidate)
        payload["curation"] = {
            "quality_score": self.quality_score,
            "flags": list(self.flags),
            "review_priority": self.review_priority,
            "exact_duplicate_of": self.exact_duplicate_of,
            "same_anchor_near_duplicate_of": self.same_anchor_near_duplicate_of,
            "cross_document_near_duplicate_ids": list(
                self.cross_document_near_duplicate_ids
            ),
        }
        return payload


def normalize_query(text: str) -> str:
    normalized = _WS.sub(" ", text.strip().lower())
    normalized = re.sub(r"[?!.]+$", "", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return _WS.sub(" ", normalized).strip()


def query_tokens(text: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in _WORD.findall(text))


def _content_tokens(text: str) -> set[str]:
    stop = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "how", "in", "is", "it", "of", "on", "or", "that", "the", "their",
        "this", "to", "what", "when", "where", "which", "who", "why", "with",
    }
    return {
        token.lower()
        for token in _WORD.findall(text)
        if len(token) >= 3 and token.lower() not in stop
    }


def token_jaccard(left: str, right: str) -> float:
    a = _content_tokens(left)
    b = _content_tokens(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def near_duplicate(left: str, right: str, *, threshold: float) -> bool:
    jaccard = token_jaccard(left, right)
    if jaccard >= threshold:
        return True
    # Character similarity catches punctuation/hyphenation variants that token
    # Jaccard can miss. Require a high bar to avoid collapsing legitimate
    # semantically related questions.
    ratio = SequenceMatcher(None, normalize_query(left), normalize_query(right)).ratio()
    return ratio >= max(0.92, threshold + 0.05)


def candidate_flags(candidate: QueryCandidate) -> tuple[str, ...]:
    flags: list[str] = []
    query_lower = candidate.query.lower()

    if any(phrase in query_lower for phrase in _BENCHMARK_PHRASES):
        flags.append("benchmark_phrase")
    if any(pattern.match(candidate.query.strip()) for pattern in _GENERIC_QUESTIONS):
        flags.append("generic_question")
    if len(candidate.query) < 28:
        flags.append("very_short_query")
    if len(candidate.query) > 200:
        flags.append("very_long_query")
    if len(candidate.answer_text) > 300:
        flags.append("long_answer")
    if len(candidate.evidence_quote) > 600:
        flags.append("long_evidence_quote")
    if candidate.repair_count > 0:
        flags.append("generated_after_repair")
    if candidate.variant == "alternate":
        flags.append("alternate_variant")
    if candidate.category == "section_concept" and candidate.section is None:
        flags.append("section_locator_missing")

    normalized_answer = normalize_query(candidate.answer_text)
    normalized_query = normalize_query(candidate.query)
    if len(normalized_answer) >= 12 and normalized_answer in normalized_query:
        flags.append("answer_leakage")

    if candidate.category == "semantic_paraphrase":
        q_tokens = _content_tokens(candidate.query)
        evidence_tokens = _content_tokens(candidate.evidence_quote)
        if q_tokens:
            copied_ratio = len(q_tokens & evidence_tokens) / len(q_tokens)
            if copied_ratio >= 0.88:
                flags.append("semantic_query_copy_like")

    return tuple(sorted(set(flags)))


def quality_score(candidate: QueryCandidate, flags: Iterable[str]) -> int:
    penalties = {
        "answer_leakage": 35,
        "generic_question": 25,
        "benchmark_phrase": 15,
        "semantic_query_copy_like": 12,
        "very_long_query": 10,
        "very_short_query": 8,
        "long_answer": 7,
        "long_evidence_quote": 5,
        "section_locator_missing": 5,
        "generated_after_repair": 3,
        "alternate_variant": 2,
        "cross_document_near_duplicate": 4,
    }
    score = 100 - sum(penalties.get(flag, 0) for flag in flags)

    # Prefer concise but not cryptic questions/answers without imposing a hard
    # benchmark-specific template.
    if 35 <= len(candidate.query) <= 150:
        score += 3
    if len(candidate.answer_text) <= 220:
        score += 2
    if candidate.variant == "primary":
        score += 1
    return max(0, min(100, score))


def _preference_key(item: CuratedCandidate) -> tuple[Any, ...]:
    candidate = item.candidate
    return (
        -item.quality_score,
        0 if candidate.variant == "primary" else 1,
        candidate.repair_count,
        len(candidate.answer_text),
        candidate.candidate_id,
    )


def _base_curated(candidates: list[QueryCandidate]) -> list[CuratedCandidate]:
    rows: list[CuratedCandidate] = []
    for candidate in candidates:
        flags = candidate_flags(candidate)
        rows.append(
            CuratedCandidate(
                candidate=candidate,
                quality_score=quality_score(candidate, flags),
                flags=flags,
                review_priority="",
            )
        )
    return rows


def exact_dedupe(
    candidates: list[CuratedCandidate],
) -> tuple[list[CuratedCandidate], list[CuratedCandidate]]:
    groups: dict[str, list[CuratedCandidate]] = defaultdict(list)
    for item in candidates:
        groups[normalize_query(item.candidate.query)].append(item)

    kept: list[CuratedCandidate] = []
    dropped: list[CuratedCandidate] = []
    for key in sorted(groups):
        group = sorted(groups[key], key=_preference_key)
        winner = group[0]
        kept.append(winner)
        for loser in group[1:]:
            dropped.append(
                CuratedCandidate(
                    candidate=loser.candidate,
                    quality_score=loser.quality_score,
                    flags=tuple(sorted(set(loser.flags) | {"exact_duplicate"})),
                    review_priority="reject",
                    exact_duplicate_of=winner.candidate.candidate_id,
                )
            )
    return kept, dropped


def same_anchor_near_dedupe(
    candidates: list[CuratedCandidate],
    *,
    threshold: float,
) -> tuple[list[CuratedCandidate], list[CuratedCandidate]]:
    by_anchor: dict[str, list[CuratedCandidate]] = defaultdict(list)
    for item in candidates:
        by_anchor[item.candidate.anchor_id].append(item)

    kept: list[CuratedCandidate] = []
    dropped: list[CuratedCandidate] = []
    for anchor_id in sorted(by_anchor):
        group = sorted(by_anchor[anchor_id], key=_preference_key)
        winners: list[CuratedCandidate] = []
        for item in group:
            duplicate_of: CuratedCandidate | None = None
            for winner in winners:
                if near_duplicate(
                    item.candidate.query,
                    winner.candidate.query,
                    threshold=threshold,
                ):
                    duplicate_of = winner
                    break
            if duplicate_of is None:
                winners.append(item)
                continue
            dropped.append(
                CuratedCandidate(
                    candidate=item.candidate,
                    quality_score=item.quality_score,
                    flags=tuple(
                        sorted(set(item.flags) | {"same_anchor_near_duplicate"})
                    ),
                    review_priority="reject",
                    same_anchor_near_duplicate_of=(
                        duplicate_of.candidate.candidate_id
                    ),
                )
            )
        kept.extend(winners)
    return kept, dropped


def mark_cross_document_near_duplicates(
    candidates: list[CuratedCandidate],
    *,
    threshold: float,
) -> list[CuratedCandidate]:
    neighbors: dict[str, list[str]] = defaultdict(list)
    ordered = sorted(candidates, key=lambda item: item.candidate.candidate_id)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if left.candidate.document_key == right.candidate.document_key:
                continue
            if near_duplicate(
                left.candidate.query,
                right.candidate.query,
                threshold=threshold,
            ):
                neighbors[left.candidate.candidate_id].append(
                    right.candidate.candidate_id
                )
                neighbors[right.candidate.candidate_id].append(
                    left.candidate.candidate_id
                )

    marked: list[CuratedCandidate] = []
    for item in candidates:
        ids = tuple(sorted(neighbors.get(item.candidate.candidate_id, [])))
        flags = set(item.flags)
        if ids:
            flags.add("cross_document_near_duplicate")
        flag_tuple = tuple(sorted(flags))
        marked.append(
            CuratedCandidate(
                candidate=item.candidate,
                quality_score=quality_score(item.candidate, flag_tuple),
                flags=flag_tuple,
                review_priority="",
                exact_duplicate_of=item.exact_duplicate_of,
                same_anchor_near_duplicate_of=item.same_anchor_near_duplicate_of,
                cross_document_near_duplicate_ids=ids,
            )
        )
    return marked


def _largest_remainder_quota(
    counts: dict[str, int],
    *,
    target: int,
) -> dict[str, int]:
    total = sum(counts.values())
    if target > total:
        raise EvaluationContractError(
            f"shortlist target {target} exceeds candidate pool {total}"
        )
    raw = {key: target * value / total for key, value in counts.items()}
    quota = {key: min(counts[key], math.floor(value)) for key, value in raw.items()}
    remaining = target - sum(quota.values())
    order = sorted(
        counts,
        key=lambda key: (-(raw[key] - math.floor(raw[key])), key),
    )
    while remaining:
        progressed = False
        for key in order:
            if quota[key] < counts[key]:
                quota[key] += 1
                remaining -= 1
                progressed = True
                if remaining == 0:
                    break
        if not progressed:
            raise AssertionError("unable to allocate category quota")
    return dict(sorted(quota.items()))


def _stable_tiebreak(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()


def _rank_key(item: CuratedCandidate) -> tuple[Any, ...]:
    return (
        -item.quality_score,
        len(item.flags),
        0 if item.candidate.variant == "primary" else 1,
        item.candidate.repair_count,
        _stable_tiebreak(item.candidate.candidate_id),
    )


def select_shortlist(
    candidates: list[CuratedCandidate],
    *,
    target: int,
) -> tuple[list[CuratedCandidate], dict[str, int]]:
    if target <= 0:
        raise EvaluationContractError("shortlist target must be positive")
    category_counts = Counter(item.candidate.category for item in candidates)
    quota = _largest_remainder_quota(dict(category_counts), target=target)

    by_document: dict[str, list[CuratedCandidate]] = defaultdict(list)
    for item in candidates:
        by_document[item.candidate.document_key].append(item)
    for document_key in by_document:
        by_document[document_key].sort(key=_rank_key)

    documents = sorted(by_document)
    selected: list[CuratedCandidate] = []
    selected_ids: set[str] = set()
    selected_categories: Counter[str] = Counter()

    # Document round-robin prevents long/high-yield documents from dominating.
    # Category quotas are enforced globally. We do not impose topic quotas because
    # multiple independent documents intentionally share topics such as zero trust.
    while len(selected) < target:
        progressed = False
        for document_key in documents:
            bucket = by_document[document_key]
            choice: CuratedCandidate | None = None
            for item in bucket:
                if item.candidate.candidate_id in selected_ids:
                    continue
                if selected_categories[item.candidate.category] >= quota[
                    item.candidate.category
                ]:
                    continue
                choice = item
                break
            if choice is None:
                continue
            selected.append(choice)
            selected_ids.add(choice.candidate.candidate_id)
            selected_categories[choice.candidate.category] += 1
            progressed = True
            if len(selected) == target:
                break
        if not progressed:
            raise EvaluationContractError(
                "could not reach shortlist target under category quotas"
            )

    return selected, quota


def assign_review_priority(item: CuratedCandidate) -> CuratedCandidate:
    high_flags = {
        "answer_leakage",
        "generic_question",
        "benchmark_phrase",
        "cross_document_near_duplicate",
        "semantic_query_copy_like",
    }
    if high_flags & set(item.flags):
        priority = "high"
    elif item.candidate.category in {"semantic_paraphrase", "section_concept"}:
        priority = "medium"
    elif item.flags:
        priority = "medium"
    else:
        priority = "low"
    return CuratedCandidate(
        candidate=item.candidate,
        quality_score=item.quality_score,
        flags=item.flags,
        review_priority=priority,
        exact_duplicate_of=item.exact_duplicate_of,
        same_anchor_near_duplicate_of=item.same_anchor_near_duplicate_of,
        cross_document_near_duplicate_ids=item.cross_document_near_duplicate_ids,
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def curate(
    *,
    candidates_path: Path,
    output_dir: Path,
    shortlist_target: int,
    near_duplicate_threshold: float,
    generation_summary_path: Path | None = None,
) -> dict[str, Any]:
    if not 0.70 <= near_duplicate_threshold <= 0.98:
        raise EvaluationContractError(
            "near duplicate threshold must be between 0.70 and 0.98"
        )
    candidates = load_candidates(candidates_path)
    base = _base_curated(candidates)

    exact_kept, exact_dropped = exact_dedupe(base)
    near_kept, near_dropped = same_anchor_near_dedupe(
        exact_kept,
        threshold=near_duplicate_threshold,
    )
    marked = mark_cross_document_near_duplicates(
        near_kept,
        threshold=near_duplicate_threshold,
    )
    marked = [assign_review_priority(item) for item in marked]

    shortlist, category_quota = select_shortlist(
        marked,
        target=shortlist_target,
    )
    shortlist = [assign_review_priority(item) for item in shortlist]

    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(
        output_dir / "query_candidates_deduped.jsonl",
        (item.to_dict() for item in sorted(marked, key=_rank_key)),
    )
    write_jsonl(
        output_dir / "query_candidates_auto_rejected.jsonl",
        (
            item.to_dict()
            for item in sorted(exact_dropped + near_dropped, key=_rank_key)
        ),
    )
    write_jsonl(
        output_dir / "query_candidates_shortlist.jsonl",
        (item.to_dict() for item in shortlist),
    )

    review_queue = sorted(
        shortlist,
        key=lambda item: (
            {"high": 0, "medium": 1, "low": 2}[item.review_priority],
            _rank_key(item),
        ),
    )
    write_jsonl(
        output_dir / "query_candidates_review_queue.jsonl",
        (item.to_dict() for item in review_queue),
    )

    shortlist_ids = {item.candidate.candidate_id for item in shortlist}
    reserve = [
        item
        for item in sorted(marked, key=_rank_key)
        if item.candidate.candidate_id not in shortlist_ids
    ]
    write_jsonl(
        output_dir / "query_candidates_reserve.jsonl",
        (item.to_dict() for item in reserve),
    )

    generation_summary: dict[str, Any] | None = None
    if generation_summary_path is not None and generation_summary_path.exists():
        payload = json.loads(generation_summary_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            generation_summary = payload

    flag_counts = Counter(flag for item in marked for flag in item.flags)
    shortlist_flag_counts = Counter(flag for item in shortlist for flag in item.flags)
    summary = {
        "curator_version": CURATOR_VERSION,
        "candidates_sha256": sha256_file(candidates_path),
        "input_candidate_count": len(candidates),
        "exact_duplicate_drop_count": len(exact_dropped),
        "same_anchor_near_duplicate_drop_count": len(near_dropped),
        "deduped_candidate_count": len(marked),
        "shortlist_target": shortlist_target,
        "shortlist_count": len(shortlist),
        "reserve_count": len(reserve),
        "near_duplicate_threshold": near_duplicate_threshold,
        "category_quota": category_quota,
        "shortlist_category_counts": dict(
            sorted(Counter(item.candidate.category for item in shortlist).items())
        ),
        "shortlist_document_counts": dict(
            sorted(Counter(item.candidate.document_key for item in shortlist).items())
        ),
        "shortlist_topic_counts": dict(
            sorted(Counter(item.candidate.topic for item in shortlist).items())
        ),
        "pool_flag_counts": dict(sorted(flag_counts.items())),
        "shortlist_flag_counts": dict(sorted(shortlist_flag_counts.items())),
        "review_priority_counts": dict(
            sorted(Counter(item.review_priority for item in shortlist).items())
        ),
        "shortlist_primary_variant_count": sum(
            item.candidate.variant == "primary" for item in shortlist
        ),
        "shortlist_alternate_variant_count": sum(
            item.candidate.variant == "alternate" for item in shortlist
        ),
        "generation_provenance": (
            {
                key: generation_summary.get(key)
                for key in (
                    "generator_version",
                    "generator_model",
                    "request_count",
                    "candidate_count",
                    "unusable_count",
                    "failure_batch_count",
                    "validation_error_count",
                    "plan_version",
                    "plan_sha256",
                    "anchors_sha256",
                )
                if key in generation_summary
            }
            if generation_summary is not None
            else None
        ),
    }
    (output_dir / "query_curation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deterministically curate grounded Document RAG query candidates."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {CURATOR_VERSION}",
    )
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--shortlist-target", type=int, default=450)
    parser.add_argument("--near-duplicate-threshold", type=float, default=0.86)
    parser.add_argument("--generation-summary", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = curate(
        candidates_path=args.candidates,
        output_dir=args.output_dir,
        shortlist_target=args.shortlist_target,
        near_duplicate_threshold=args.near_duplicate_threshold,
        generation_summary_path=args.generation_summary,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
