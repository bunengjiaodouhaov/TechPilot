import asyncio
import time
from typing import Any

import httpx
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import settings
from app.db.session import AsyncSessionLocal


async def check_postgres() -> dict[str, Any]:
    started = time.perf_counter()

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))

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
        async with httpx.AsyncClient(timeout=5.0) as client:
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