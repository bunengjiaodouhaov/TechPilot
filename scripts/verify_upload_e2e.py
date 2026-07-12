from __future__ import annotations

import asyncio
import sys
from io import BytesIO
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from httpx import ASGITransport, AsyncClient
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)
from sqlalchemy import delete, func, select

from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.workspace import Workspace


WORKSPACE_NAME = "Day3 E2E Verification"


def build_test_pdf() -> bytes:
    """Create a small PDF whose text can be extracted by pypdf."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)

    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): font_reference,
                }
            )
        }
    )

    content = DecodedStreamObject()
    content.set_data(
        b"BT /F1 12 Tf 72 720 Td "
        b"(Hello TechPilot PDF ingestion.) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(content)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


async def create_workspace() -> int:
    async with AsyncSessionLocal() as session:
        workspace = Workspace(name=WORKSPACE_NAME)
        session.add(workspace)
        await session.commit()
        await session.refresh(workspace)

        print(f"Created temporary workspace: {workspace.id}")
        return workspace.id


async def delete_workspace(workspace_id: int) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(Workspace).where(Workspace.id == workspace_id)
        )
        await session.commit()

    print("Deleted temporary workspace and cascaded ingestion data.")


async def upload_test_documents(workspace_id: int) -> None:
    transport = ASGITransport(app=app)

    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        markdown_response = await client.post(
            "/documents/upload",
            data={"workspace_id": str(workspace_id)},
            files={
                "file": (
                    "day3-guide.md",
                    (
                        b"# TechPilot Day 3\n\n"
                        b"Document ingestion is working.\n"
                    ),
                    "text/markdown",
                )
            },
        )

        print(
            "Markdown response:",
            markdown_response.status_code,
            markdown_response.json(),
        )

        if markdown_response.status_code != 201:
            raise RuntimeError(
                "Markdown upload failed: "
                f"{markdown_response.text}"
            )

        pdf_response = await client.post(
            "/documents/upload",
            data={"workspace_id": str(workspace_id)},
            files={
                "file": (
                    "day3-paper.pdf",
                    build_test_pdf(),
                    "application/pdf",
                )
            },
        )

        print(
            "PDF response:",
            pdf_response.status_code,
            pdf_response.json(),
        )

        if pdf_response.status_code != 201:
            raise RuntimeError(
                f"PDF upload failed: {pdf_response.text}"
            )


async def verify_database(workspace_id: int) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Document)
            .where(Document.workspace_id == workspace_id)
            .order_by(Document.id)
        )
        documents = list(result.scalars())

        if len(documents) != 2:
            raise RuntimeError(
                f"Expected 2 documents, found {len(documents)}."
            )

        print("\nDatabase verification:")

        for document in documents:
            chunk_count = await session.scalar(
                select(func.count(Chunk.id)).where(
                    Chunk.document_id == document.id
                )
            )

            chunks_result = await session.execute(
                select(Chunk)
                .where(Chunk.document_id == document.id)
                .order_by(Chunk.chunk_index)
            )
            chunks = list(chunks_result.scalars())

            print(
                {
                    "document_id": document.id,
                    "name": document.name,
                    "file_type": document.file_type,
                    "status": document.status,
                    "checksum_length": len(document.checksum),
                    "chunk_count": chunk_count,
                }
            )

            if document.status != "COMPLETED":
                raise RuntimeError(
                    f"{document.name} status is {document.status}."
                )

            if not chunks:
                raise RuntimeError(
                    f"{document.name} has no persisted chunks."
                )

            for chunk in chunks:
                if chunk.char_count != len(chunk.text):
                    raise RuntimeError(
                        f"Invalid char_count for chunk {chunk.chunk_id}."
                    )

                if not isinstance(chunk.metadata_json, dict):
                    raise RuntimeError(
                        f"Invalid metadata for chunk {chunk.chunk_id}."
                    )

                print(
                    {
                        "chunk_index": chunk.chunk_index,
                        "chunk_id_prefix": chunk.chunk_id[:12],
                        "page_start": chunk.page_start,
                        "page_end": chunk.page_end,
                        "section": chunk.section,
                        "char_count": chunk.char_count,
                        "metadata": chunk.metadata_json,
                    }
                )


async def main() -> None:
    workspace_id = await create_workspace()

    try:
        await upload_test_documents(workspace_id)
        await verify_database(workspace_id)
        print("\nE2E RESULT: PASS")
    finally:
        await delete_workspace(workspace_id)
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
