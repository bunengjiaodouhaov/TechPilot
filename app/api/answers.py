from fastapi import APIRouter, Depends, HTTPException, status

from app.answering.answer_service import (
    AnswerService,
    WorkspaceNotFoundError,
)
from app.api.dependencies import get_answer_service
from app.api.schemas.answer import (
    AnswerRequest,
    AnswerResponse,
    CitationResponse,
)

router = APIRouter(prefix="/answers", tags=["answers"])


@router.post("", response_model=AnswerResponse)
async def answer_question(
    request: AnswerRequest,
    service: AnswerService = Depends(get_answer_service),
) -> AnswerResponse:
    """Answer a question using evidence from one workspace."""
    try:
        result = await service.answer(
            workspace_id=request.workspace_id,
            question=request.question,
        )
    except WorkspaceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="workspace not found",
        ) from exc

    return AnswerResponse(
        question=result.question,
        answer=result.text,
        citations=[
            CitationResponse(
                document_name=citation.document_name,
                page_start=citation.page_start,
                page_end=citation.page_end,
                section=citation.section,
                quote=citation.quote,
            )
            for citation in result.citations
        ],
        refused=result.refused,
    )
