import asyncio
import time
from typing import Any

import httpx
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.network import should_trust_proxy_environment

_postgres_health_engine = create_async_engine(
    settings.database_url,
    poolclass=NullPool,
)



async def check_postgres() -> dict[str, Any]:
    """Probe PostgreSQL without touching the application pooled connections."""
    started = time.perf_counter()

    try:
        async with _postgres_health_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

        return {
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception as exc:
        return {
            "status": "error",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": type(exc).__name__,
        }


async def check_redis() -> dict[str, Any]:
    started = time.perf_counter()
    client = Redis.from_url(settings.redis_url)

    try:
        await client.ping()

        return {
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception as exc:
        return {
            "status": "error",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": type(exc).__name__,
        }
    finally:
        await client.aclose()


async def check_qdrant() -> dict[str, Any]:
    started = time.perf_counter()

    try:
        async with httpx.AsyncClient(
            timeout=5.0,
            trust_env=should_trust_proxy_environment(settings.qdrant_url),
        ) as client:
            response = await client.get(f"{settings.qdrant_url}/healthz")
            response.raise_for_status()

        return {
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception as exc:
        return {
            "status": "error",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": type(exc).__name__,
        }


async def check_dependencies() -> dict[str, dict[str, Any]]:
    postgres, redis, qdrant = await asyncio.gather(
        check_postgres(),
        check_redis(),
        check_qdrant(),
    )

    return {
        "postgres": postgres,
        "redis": redis,
        "qdrant": qdrant,
    }