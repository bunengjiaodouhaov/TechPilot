from fastapi import Depends, FastAPI, Response, status

from app.api.answers import router as answers_router
from app.api.conversation_history import router as conversation_history_router
from app.api.documents import router as documents_router
from app.api.jd import router as jd_router
from app.api.job_sources import router as job_sources_router
from app.api.jobs import router as jobs_router
from app.api.product_memory import router as product_memory_router
from app.api.repository_product import router as repository_product_router
from app.api.workspaces import router as workspaces_router
from app.auth.dependencies import get_current_user
from app.auth.router import router as auth_router
from app.product_ui.router import router as product_ui_router
from app.services.health import check_dependencies


app = FastAPI(
    title="TechPilot API",
    description="Technical research and code understanding platform.",
    version="0.1.0",
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "techpilot"}


@app.get("/health/dependencies", tags=["system"])
async def dependencies_health(response: Response) -> dict[str, object]:
    dependencies = await check_dependencies()
    all_healthy = all(
        dependency["status"] == "ok"
        for dependency in dependencies.values()
    )
    overall_status = "ok" if all_healthy else "degraded"
    if not all_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": overall_status, "dependencies": dependencies}


app.include_router(auth_router)
app.include_router(workspaces_router)
app.include_router(product_ui_router)
app.include_router(conversation_history_router)
app.include_router(
    repository_product_router,
    dependencies=[Depends(get_current_user)],
)
app.include_router(product_memory_router)
app.include_router(answers_router)
app.include_router(documents_router)
app.include_router(jd_router)
app.include_router(jobs_router)
app.include_router(job_sources_router)
