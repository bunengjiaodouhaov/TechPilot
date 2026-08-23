import asyncio

from app.resume.extractor import (
    ResumeExtractor,
)


def test_resume_extraction():

    result = asyncio.run(
        ResumeExtractor().extract(
            """
            Python backend engineer.
            Experienced in FastAPI and RAG.
            """
        )
    )


    names = [
        item.name
        for item in result.skills
    ]


    assert "python" in names

    assert "fastapi" in names

    assert "rag" in names
