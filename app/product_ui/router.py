from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(include_in_schema=False)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@router.get("/")
async def product_home() -> FileResponse:
    """Serve the TechPilot product shell."""
    return FileResponse(
        _STATIC_DIR / "index.html",
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/ui/styles.css")
async def product_styles() -> FileResponse:
    """Serve the TechPilot product stylesheet."""
    return FileResponse(
        _STATIC_DIR / "styles.css",
        media_type="text/css; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/ui/app.js")
async def product_script() -> FileResponse:
    """Serve the TechPilot product client."""
    return FileResponse(
        _STATIC_DIR / "app.js",
        media_type="text/javascript; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )

# CLOSEOUT_UI_V1
@router.get("/ui/closeout.css")
async def product_closeout_styles() -> FileResponse:
    """Serve the portfolio closeout UI layer."""
    return FileResponse(
        _STATIC_DIR / "closeout.css",
        media_type="text/css; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/ui/closeout.js")
async def product_closeout_script() -> FileResponse:
    """Serve demo access and localization behavior."""
    return FileResponse(
        _STATIC_DIR / "closeout.js",
        media_type="text/javascript; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/ui/product-memory.js")
async def product_memory_script() -> FileResponse:
    return FileResponse(
        _STATIC_DIR / "product-memory.js",
        media_type="text/javascript; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/ui/code-rag.js")
async def code_rag_script() -> FileResponse:
    return FileResponse(
        _STATIC_DIR / "code-rag.js",
        media_type="text/javascript; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/ui/code-rag.css")
async def code_rag_styles() -> FileResponse:
    return FileResponse(
        _STATIC_DIR / "code-rag.css",
        media_type="text/css; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/ui/conversation-ui.js")
async def conversation_ui_script() -> FileResponse:
    return FileResponse(
        _STATIC_DIR / "conversation-ui.js",
        media_type="text/javascript; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )
