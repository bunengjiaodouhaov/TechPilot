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
