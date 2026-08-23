from pydantic import BaseModel, Field


class ResumeSkill(BaseModel):

    name: str

    level: str = "medium"


class StructuredResume(BaseModel):

    skills: list[ResumeSkill] = Field(
        default_factory=list
    )

    years_experience: float | None = None
