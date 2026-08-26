from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.user import User


@pytest.mark.asyncio
async def test_registration_cookie_login_and_logout_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = f"p6-register-{uuid4().hex}@example.com"
    password = "integration-password"

    # Keep this integration test independent of pooled asyncpg connections
    # created by an earlier pytest event loop, and use an RFC-sized test key.
    await engine.dispose()
    monkeypatch.setattr(
        settings,
        "auth_secret_key",
        "integration-test-signing-key-32-bytes-minimum",
    )

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            register_response = await client.post(
                "/auth/register",
                json={"email": email, "password": password},
            )
            assert register_response.status_code == 201
            register_body = register_response.json()
            assert register_body["token_type"] == "bearer"
            assert register_body["access_token"]
            assert register_body["expires_in_seconds"] > 0
            assert "techpilot_access_token" in client.cookies

            me_response = await client.get("/auth/me")
            assert me_response.status_code == 200
            me_body = me_response.json()
            assert me_body["email"] == email
            assert me_body["is_demo"] is False

            duplicate_response = await client.post(
                "/auth/register",
                json={"email": email, "password": password},
            )
            assert duplicate_response.status_code == 409

            logout_response = await client.post("/auth/logout")
            assert logout_response.status_code == 204

            after_logout = await client.get("/auth/me")
            assert after_logout.status_code == 401
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(User).where(User.email == email))
            await session.commit()
        await engine.dispose()
