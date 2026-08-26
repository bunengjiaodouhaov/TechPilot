from app.answering.dto import StoredChunk
from app.answering.recovery_answer_service import BoundaryAwareAnswerService


def stored(
    *,
    chunk_db_id: int,
    chunk_index: int,
    section: str,
) -> StoredChunk:
    return StoredChunk(
        chunk_db_id=chunk_db_id,
        chunk_id=f"chunk-{chunk_db_id}",
        document_id=10,
        document_name="enterprise.docx",
        source_type="docx",
        chunk_index=chunk_index,
        section=section,
        page_start=None,
        page_end=None,
        text=f"evidence at chunk {chunk_index}",
    )


def test_boundary_policy_accepts_only_immediate_cross_section_neighbor() -> None:
    mcp_parent = "五、MCP Gateway v2：第三核心模块"
    hitl_parent = "六、HITL / Reliability / Checkpoint"
    selected_groups = [
        (
            (10, mcp_parent),
            ((6, 36), (16, 37), (20, 44)),
        )
    ]
    q38 = stored(
        chunk_db_id=201,
        chunk_index=39,
        section=mcp_parent + " > Q38. READ、WRITE、DANGEROUS",
    )
    q40 = stored(
        chunk_db_id=202,
        chunk_index=41,
        section=mcp_parent + " > Q40. Tool Schema",
    )
    q44 = stored(
        chunk_db_id=203,
        chunk_index=45,
        section=hitl_parent + " > Q44. HITL",
    )
    q45 = stored(
        chunk_db_id=204,
        chunk_index=46,
        section=hitl_parent + " > Q45. Reliability",
    )
    previous_section = stored(
        chunk_db_id=205,
        chunk_index=34,
        section="四、Memory v2：第二核心模块 > Q33. Memory",
    )

    ranked = BoundaryAwareAnswerService._rank_recovery_chunks(
        chunks=[q38, q40, q44, q45, previous_section],
        selected_groups=selected_groups,
        exclude_chunk_ids=set(),
    )

    assert [chunk.chunk_index for chunk in ranked] == [39, 41, 45]
    assert ranked[-1].section == hitl_parent + " > Q44. HITL"


def test_boundary_policy_still_excludes_explicit_anchor_ids() -> None:
    parent = "五、MCP Gateway v2：第三核心模块"
    anchor_chunk = stored(
        chunk_db_id=301,
        chunk_index=44,
        section=parent + " > Q43. server failure",
    )

    ranked = BoundaryAwareAnswerService._rank_recovery_chunks(
        chunks=[anchor_chunk],
        selected_groups=[((10, parent), ((20, 44),))],
        exclude_chunk_ids={301},
    )

    assert ranked == []
