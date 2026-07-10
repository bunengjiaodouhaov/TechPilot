from fastapi import FastAPI

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
