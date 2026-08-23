from pydantic import BaseModel, Field


class JDAnalyzeRequest(BaseModel):
    jd: str = Field(min_length=1)
