from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints


QuestionText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=1000,
    ),
]


class AnswerRequest(BaseModel):
    workspace_id: int = Field(gt=0)
    question: QuestionText


class CitationResponse(BaseModel):
    document_name: str
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    quote: str


class AnswerResponse(BaseModel):
    question: str
    answer: str
    citations: list[CitationResponse]
    refused: bool
