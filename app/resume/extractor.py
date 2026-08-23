from app.resume.schemas import (
    StructuredResume,
    ResumeSkill,
)


class ResumeExtractor:


    async def extract(
        self,
        text: str,
    ) -> StructuredResume:

        normalized = text.lower()

        skills = []


        for skill in [
            "python",
            "fastapi",
            "rag",
            "llm",
            "postgresql",
        ]:

            if skill in normalized:

                skills.append(
                    ResumeSkill(
                        name=skill,
                        level="strong",
                    )
                )


        return StructuredResume(
            skills=skills
        )
