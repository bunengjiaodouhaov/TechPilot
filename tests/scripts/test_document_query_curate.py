from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.document_query_candidates import QueryCandidate
from scripts.document_query_curate import (
    CuratedCandidate,
    assign_review_priority,
    candidate_flags,
    exact_dedupe,
    near_duplicate,
    quality_score,
    same_anchor_near_dedupe,
    select_shortlist,
)


def _candidate(
    *,
    candidate_id: str,
    query: str,
    document_key: str = "doc-a",
    anchor_id: str = "anchor-a",
    category: str = "direct_fact",
    variant: str = "primary",
    repair_count: int = 0,
) -> QueryCandidate:
    return QueryCandidate(
        candidate_id=candidate_id,
        request_id=f"request-{candidate_id}",
        anchor_id=anchor_id,
        document_key=document_key,
        topic=f"topic-{document_key}",
        page=1,
        section="Section",
        source_unit_sha256="a" * 64,
        category=category,
        variant=variant,
        query=query,
        answer_text="A concise supported answer.",
        evidence_quote="This is authoritative evidence supporting the concise answer.",
        generation_mode="llm_batch",
        generator_model="fake",
        batch_id="b1",
        repair_count=repair_count,
    )


def _curated(candidate: QueryCandidate) -> CuratedCandidate:
    flags = candidate_flags(candidate)
    return assign_review_priority(
        CuratedCandidate(
            candidate=candidate,
            quality_score=quality_score(candidate, flags),
            flags=flags,
            review_priority="",
        )
    )


def test_exact_duplicate_keeps_preferred_candidate() -> None:
    primary = _curated(
        _candidate(
            candidate_id="c1",
            query="What control is required for incident response?",
        )
    )
    repaired_alt = _curated(
        _candidate(
            candidate_id="c2",
            query="What control is required for incident response?",
            variant="alternate",
            repair_count=1,
        )
    )
    kept, dropped = exact_dedupe([repaired_alt, primary])
    assert [item.candidate.candidate_id for item in kept] == ["c1"]
    assert dropped[0].exact_duplicate_of == "c1"


def test_same_anchor_near_duplicate_drops_redundant_variant() -> None:
    first = _curated(
        _candidate(
            candidate_id="c1",
            query="What should organizations maintain before an incident occurs?",
        )
    )
    second = _curated(
        _candidate(
            candidate_id="c2",
            query="What should an organization maintain before an incident occurs?",
            variant="alternate",
        )
    )
    kept, dropped = same_anchor_near_dedupe(
        [first, second], threshold=0.80
    )
    assert len(kept) == 1
    assert len(dropped) == 1
    assert dropped[0].same_anchor_near_duplicate_of == kept[0].candidate.candidate_id


def test_near_duplicate_does_not_merge_distinct_questions() -> None:
    assert not near_duplicate(
        "Who is responsible for approving the security plan?",
        "When must the security plan be reviewed?",
        threshold=0.86,
    )


def test_benchmark_phrase_is_flagged_not_silently_deleted() -> None:
    candidate = _candidate(
        candidate_id="c1",
        query="According to the excerpt, what control is required?",
    )
    flags = candidate_flags(candidate)
    assert "benchmark_phrase" in flags


def test_shortlist_balances_documents_and_respects_category_quota() -> None:
    rows: list[CuratedCandidate] = []
    for doc_index in range(3):
        for item_index in range(6):
            category = "direct_fact" if item_index < 3 else "semantic_paraphrase"
            rows.append(
                _curated(
                    _candidate(
                        candidate_id=f"c-{doc_index}-{item_index}",
                        query=(
                            f"What requirement {doc_index}-{item_index} applies to "
                            "the organization?"
                        ),
                        document_key=f"doc-{doc_index}",
                        anchor_id=f"a-{doc_index}-{item_index}",
                        category=category,
                    )
                )
            )
    selected, quota = select_shortlist(rows, target=12)
    assert len(selected) == 12
    assert quota == {"direct_fact": 6, "semantic_paraphrase": 6}
    doc_counts = {}
    for item in selected:
        doc_counts[item.candidate.document_key] = (
            doc_counts.get(item.candidate.document_key, 0) + 1
        )
    assert doc_counts == {"doc-0": 4, "doc-1": 4, "doc-2": 4}


def test_direct_script_version_works_without_pythonpath() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    script = repository_root / "scripts/document_query_curate.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--version"],
        cwd=repository_root,
        env={"PATH": str(Path(sys.executable).parent)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "document-query-curator-v1" in completed.stdout
