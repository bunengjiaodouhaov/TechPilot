from fastapi import FastAPI, Response, status

from app.api.answers import router as answers_router
from app.api.documents import router as documents_router
from app.api.jd import router as jd_router
from app.api.workspaces import router as workspaces_router
from app.product_ui.router import router as product_ui_router
from app.services.health import check_dependencies


app = FastAPI(
    title="TechPilot API",
    description="Technical research and code understanding platform.",
    version="0.1.0",
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Return the basic service health status."""
    return {
        "status": "ok",
        "service": "techpilot",
    }


@app.get("/health/dependencies", tags=["system"])
async def dependencies_health(response: Response) -> dict[str, object]:
    """Return the health status of external dependencies."""
    dependencies = await check_dependencies()

    all_healthy = all(
        dependency["status"] == "ok"
        for dependency in dependencies.values()
    )

    if all_healthy:
        overall_status = "ok"
    else:
        overall_status = "degraded"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": overall_status,
        "dependencies": dependencies,
    }


app.include_router(workspaces_router)
app.include_router(product_ui_router)
app.include_router(answers_router)
app.include_router(documents_router)
app.include_router(jd_router)
