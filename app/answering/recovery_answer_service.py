from app.answering.answer_service import AnswerService
from app.answering.dto import StoredChunk


class BoundaryAwareAnswerService(AnswerService):
    """AnswerService with one bounded cross-section recovery bridge.

    Parent-section siblings remain the primary recovery signal. An authoritative
    chunk from a neighboring section is eligible only when it is exactly one
    chunk away from an anchor in a selected parent group. This lets evidence
    cross a heading boundary without flattening document structure or widening
    recovery to arbitrary nearby chunks.
    """

    @staticmethod
    def _rank_recovery_chunks(
        *,
        chunks: list[StoredChunk],
        selected_groups: list[
            tuple[
                tuple[int, str],
                tuple[tuple[int, int], ...],
            ]
        ],
        exclude_chunk_ids: set[int],
    ) -> list[StoredChunk]:
        scored: list[
            tuple[
                tuple[int, int, int, int, int, int],
                StoredChunk,
            ]
        ] = []

        for chunk in chunks:
            if chunk.chunk_db_id in exclude_chunk_ids:
                continue

            for group_order, ((document_id, parent), anchors) in enumerate(
                selected_groups
            ):
                if chunk.document_id != document_id:
                    continue

                distance = min(
                    abs(chunk.chunk_index - anchor_index)
                    for _, anchor_index in anchors
                )
                belongs_to_parent = AnswerService._belongs_to_parent(
                    section=chunk.section,
                    parent=parent,
                )
                is_boundary_bridge = not belongs_to_parent and distance == 1
                if not belongs_to_parent and not is_boundary_bridge:
                    continue

                support_count = len(anchors)
                best_anchor_rank = min(rank for rank, _ in anchors)
                scored.append(
                    (
                        (
                            group_order,
                            0 if belongs_to_parent else 1,
                            -support_count,
                            best_anchor_rank,
                            distance,
                            chunk.chunk_index,
                        ),
                        chunk,
                    )
                )
                break

        scored.sort(key=lambda item: item[0])
        return [chunk for _, chunk in scored]
